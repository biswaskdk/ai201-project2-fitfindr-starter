"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Usage:
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import json
import re

from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    compare_price,
    _get_groq_client,
    _llm_text,
    _tokenize,
    GROQ_MODEL,
)

# Words in a query that signal the user cares about whether the price is fair.
_PRICE_INTENT_WORDS = (
    "price", "deal", "worth", "fair", "cheap", "expensive", "budget",
    "afford", "overpriced", "$", "cost",
)


def _has_price_intent(query: str) -> bool:
    q = (query or "").lower()
    return any(w in q for w in _PRICE_INTENT_WORDS)


# ── intent routing (hybrid: keyword rules, LLM fallback) ───────────────────────
# Which tools run depends on what the user actually wants:
#   full         → search → [compare] → suggest → fitcard
#   outfit_only  → suggest (from wardrobe) → fitcard
#   price_check  → search → compare
#   caption_only → fitcard (from described look)

_INTENTS = ("full", "outfit_only", "price_check", "caption_only")

_CAPTION_WORDS = (
    "caption", "fit card", "fitcard", "write a post", "ig caption",
    "instagram caption", "tiktok caption",
)
_PRICE_Q_WORDS = (
    "good deal", "worth it", "is it worth", "fair price", "overpriced",
    "price check", "a steal", "good price", "too expensive", "is this fair",
)
_OUTFIT_WORDS = (
    "outfit idea", "how to wear", "how do i wear", "how would i wear",
    "how should i wear", "what to wear", "what do i wear", "what should i wear",
    "style my", "style this", "wear with", "put together", "ways to wear",
)
_FIND_WORDS = (
    "find", "search", "looking for", "look for", "show me", "buy", "get me",
    "i want a", "i need a", "under $", "in size",
)


def _llm_classify_intent(query: str) -> str | None:
    """LLM fallback intent classifier — returns one of _INTENTS or None on failure."""
    prompt = (
        "Classify this thrift-shopping request into exactly ONE intent and reply with "
        "only that word:\n"
        "- full: wants to find a secondhand item and get styling/caption\n"
        "- outfit_only: just wants outfit ideas from their existing wardrobe\n"
        "- price_check: just wants to know if an item's price is fair\n"
        "- caption_only: just wants a caption for a look they describe\n\n"
        f"Request: {query}\n\nIntent:"
    )
    try:
        text = _llm_text(prompt, 0, 10).lower()
        for intent in _INTENTS:
            if intent in text:
                return intent
    except Exception:
        return None
    return None


def _classify_intent(query: str) -> str:
    """
    Decide which intent a query expresses. Strong, unambiguous keyword signals
    resolve directly; conflicting or absent signals fall back to the LLM.
    """
    q = (query or "").lower()
    has_caption = any(w in q for w in _CAPTION_WORDS)
    has_price = any(w in q for w in _PRICE_Q_WORDS)
    has_outfit = any(w in q for w in _OUTFIT_WORDS)
    has_find = any(w in q for w in _FIND_WORDS)

    if has_caption and not has_find:
        return "caption_only"
    if has_price and not has_outfit and not has_caption:
        return "price_check"
    if has_outfit and not has_find and not has_price and not has_caption:
        return "outfit_only"
    if has_find and not has_outfit and not has_price and not has_caption:
        return "full"
    # Nothing matched, or signals conflict → let the LLM decide.
    return _llm_classify_intent(query) or "full"


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict, preferences: str | None = None) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.
    """
    return {
        "query": query,              # original user query
        "preferences": preferences,  # remembered style notes (optional)
        "intent": None,              # full / outfit_only / price_check / caption_only
        "parsed": None,              # extracted description / size / max_price
        "price_intent": False,       # did the query ask about price?
        "searched": False,           # has search_listings run yet?
        "search_results": [],        # list of matching listing dicts
        "adjustments": [],           # fallback constraints loosened (stretch)
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "price_verdict": None,       # dict from compare_price (stretch)
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── query parsing (hybrid: regex first, LLM fallback) ──────────────────────────

_PRICE_RE = re.compile(
    r"(?:under|below|less than|max(?:imum)?|cheaper than|<=?)\s*\$?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PRICE_DOLLAR_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
_SIZE_RE = re.compile(r"\bsize\s+([\w/.]+)", re.IGNORECASE)


def _llm_parse(query: str) -> dict | None:
    """LLM fallback parser — returns parsed dict or None if it fails."""
    prompt = (
        "Extract search parameters from this thrift-shopping request and return "
        'ONLY JSON with keys "description" (string of keywords), "size" (string or '
        'null), and "max_price" (number or null).\n\n'
        f"Request: {query}"
    )
    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
        )
        text = (resp.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group(0))
        return {
            "description": (data.get("description") or query).strip(),
            "size": data.get("size"),
            "max_price": data.get("max_price"),
        }
    except Exception:
        return None


def _parse_query(query: str) -> dict:
    """
    Parse a natural-language query into {description, size, max_price}.

    Hybrid strategy: regex extracts size and price ceiling (reliable, free);
    the remaining words become the description. If regex leaves no usable
    description, fall back to the LLM parser.
    """
    desc = query
    size = None
    max_price = None

    price_match = _PRICE_RE.search(query) or _PRICE_DOLLAR_RE.search(query)
    if price_match:
        max_price = float(price_match.group(1))
        desc = desc.replace(price_match.group(0), " ")

    size_match = _SIZE_RE.search(query)
    if size_match:
        size = size_match.group(1)
        desc = desc.replace(size_match.group(0), " ")

    desc = re.sub(r"\s+", " ", desc).strip(" ,.")

    if not _tokenize(desc):  # regex stripped everything meaningful → ask the LLM
        parsed = _llm_parse(query)
        if parsed:
            return parsed
        return {"description": query.strip(), "size": size, "max_price": max_price}

    return {"description": desc, "size": size, "max_price": max_price}


# ── planning loop ─────────────────────────────────────────────────────────────

def _decide_next_step(session: dict) -> str:
    """
    The heart of the planning loop: inspect the current session state and decide
    what to do next. This is what makes the agent conditional rather than a fixed
    sequence — e.g. it routes to 'handle_empty' (and skips the styling tools)
    when search returns nothing.
    """
    if session["parsed"] is None:
        return "parse"

    intent = session["intent"]

    # caption_only: no search, no styling — just caption the described look.
    if intent == "caption_only":
        if session["fit_card"] is None:
            return "fitcard_from_query"
        return "done"

    # outfit_only: no search — style the existing wardrobe, then caption it.
    if intent == "outfit_only":
        if session["outfit_suggestion"] is None:
            return "suggest"
        if session["fit_card"] is None:
            return "fitcard"
        return "done"

    # full and price_check both need a listing, so they search first.
    if not session["searched"]:
        return "search"
    if not session["search_results"]:        # current search returned nothing
        # Stretch: retry with loosened constraints before giving up.
        p = session["parsed"]
        if p.get("size") is not None:
            return "retry_drop_size"
        if p.get("max_price") is not None:
            return "retry_drop_price"
        return "handle_empty"
    if session["selected_item"] is None:
        return "select"

    if intent == "price_check":
        if session["price_verdict"] is None:
            return "compare"
        return "done"

    # full flow
    if session["price_intent"] and session["price_verdict"] is None:
        return "compare"
    if session["outfit_suggestion"] is None:
        return "suggest"
    if session["fit_card"] is None:
        return "fitcard"
    return "done"


def _empty_message(session: dict) -> str:
    """Build a helpful message for the no-results branch."""
    parsed = session["parsed"]
    msg = f'No listings matched "{parsed["description"]}"'
    if session["adjustments"]:
        msg += f" — even after I {', and '.join(session['adjustments'])}"
    return msg + ". Try broader keywords or a different item."


def run_agent(query: str, wardrobe: dict, preferences: str | None = None) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Check session["error"] first — if it is not None, the interaction ended
    early and the other output fields (outfit_suggestion, fit_card) will be None.
    """
    session = _new_session(query, wardrobe, preferences)

    if not query or not query.strip():
        session["error"] = "Please describe what you're looking for."
        return session

    done = False
    while not done:
        step = _decide_next_step(session)

        if step == "parse":
            session["parsed"] = _parse_query(session["query"])
            session["price_intent"] = _has_price_intent(session["query"])
            session["intent"] = _classify_intent(session["query"])

        elif step == "search":
            p = session["parsed"]
            session["search_results"] = search_listings(
                p["description"], p.get("size"), p.get("max_price")
            )
            session["searched"] = True

        elif step == "retry_drop_size":
            # Stretch (retry-with-fallback): loosen the size filter and re-search.
            session["parsed"]["size"] = None
            session["adjustments"].append("removed the size filter")
            p = session["parsed"]
            session["search_results"] = search_listings(
                p["description"], p.get("size"), p.get("max_price")
            )

        elif step == "retry_drop_price":
            # Stretch (retry-with-fallback): loosen the price ceiling and re-search.
            session["parsed"]["max_price"] = None
            session["adjustments"].append("removed the price limit")
            p = session["parsed"]
            session["search_results"] = search_listings(
                p["description"], p.get("size"), p.get("max_price")
            )

        elif step == "handle_empty":
            # Branch: nothing matched (even after fallbacks) → stop. Do NOT call
            # the styling tools with empty input. fit_card stays None.
            session["error"] = _empty_message(session)
            done = True

        elif step == "select":
            session["selected_item"] = session["search_results"][0]

        elif step == "compare":
            # Stretch (price comparison): only runs when the query asked about price.
            session["price_verdict"] = compare_price(session["selected_item"])

        elif step == "suggest":
            session["outfit_suggestion"] = suggest_outfit(
                session["selected_item"],
                session["wardrobe"],
                preferences=session["preferences"],
            )

        elif step == "fitcard":
            session["fit_card"] = create_fit_card(
                session["outfit_suggestion"], session["selected_item"]
            )

        elif step == "fitcard_from_query":
            # caption_only: caption the look the user described in their query.
            session["fit_card"] = create_fit_card(session["query"], None)

        else:  # "done"
            done = True

    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Windows consoles default to cp1252 and can't print emoji in fit cards.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee (with price intent) ===\n")
    session = run_agent(
        query="is a vintage graphic tee under $30 a good deal?",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Parsed: {session['parsed']}")
        print(f"Found: {session['selected_item']['title']} "
              f"(id={session['selected_item']['id']})")
        print(f"Price verdict: {session['price_verdict']['verdict']} "
              f"(${session['price_verdict']['item_price']:g} vs "
              f"${session['price_verdict']['median_comparable']} median, "
              f"n={session['price_verdict']['sample_size']})")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== Retry-with-fallback: impossible size, loosened ===\n")
    session_fb = run_agent(
        query="vintage graphic tee size XXL",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Adjustments: {session_fb['adjustments']}")
    print(f"Found after fallback: "
          f"{session_fb['selected_item']['title'] if session_fb['selected_item'] else None}")

    print("\n\n=== No-results path (no price intent → compare skipped) ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
    print(f"price_verdict (should be None): {session2['price_verdict']}")
    print(f"fit_card is None: {session2['fit_card'] is None}")
    print(f"outfit_suggestion is None: {session2['outfit_suggestion'] is None}")
