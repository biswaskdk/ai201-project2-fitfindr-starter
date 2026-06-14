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
    _get_groq_client,
    _tokenize,
    GROQ_MODEL,
)


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.
    """
    return {
        "query": query,              # original user query
        "parsed": None,              # extracted description / size / max_price
        "searched": False,           # has search_listings run yet?
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
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
    if not session["searched"]:
        return "search"
    if not session["search_results"]:        # searched, but nothing matched
        return "handle_empty"
    if session["selected_item"] is None:
        return "select"
    if session["outfit_suggestion"] is None:
        return "suggest"
    if session["fit_card"] is None:
        return "fitcard"
    return "done"


def _empty_message(parsed: dict) -> str:
    """Build a helpful message for the no-results branch."""
    bits = [f'"{parsed["description"]}"']
    if parsed.get("size"):
        bits.append(f'size {parsed["size"]}')
    if parsed.get("max_price") is not None:
        bits.append(f'under ${parsed["max_price"]:g}')
    return (
        f"No listings matched {', '.join(bits)}. "
        "Try broader keywords, or remove the size or price filter."
    )


def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Check session["error"] first — if it is not None, the interaction ended
    early and the other output fields (outfit_suggestion, fit_card) will be None.
    """
    session = _new_session(query, wardrobe)

    if not query or not query.strip():
        session["error"] = "Please describe what you're looking for."
        return session

    done = False
    while not done:
        step = _decide_next_step(session)

        if step == "parse":
            session["parsed"] = _parse_query(session["query"])

        elif step == "search":
            p = session["parsed"]
            session["search_results"] = search_listings(
                p["description"], p.get("size"), p.get("max_price")
            )
            session["searched"] = True

        elif step == "handle_empty":
            # Branch: nothing matched → stop. Do NOT call the styling tools
            # with empty input. fit_card stays None.
            session["error"] = _empty_message(session["parsed"])
            done = True

        elif step == "select":
            session["selected_item"] = session["search_results"][0]

        elif step == "suggest":
            session["outfit_suggestion"] = suggest_outfit(
                session["selected_item"], session["wardrobe"]
            )

        elif step == "fitcard":
            session["fit_card"] = create_fit_card(
                session["outfit_suggestion"], session["selected_item"]
            )

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

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Parsed: {session['parsed']}")
        print(f"Found: {session['selected_item']['title']} "
              f"(id={session['selected_item']['id']})")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
    print(f"fit_card is None: {session2['fit_card'] is None}")
    print(f"outfit_suggestion is None: {session2['outfit_suggestion'] is None}")
