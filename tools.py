"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re
import statistics

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

# LLM model used by suggest_outfit and create_fit_card.
GROQ_MODEL = "llama-3.3-70b-versatile"


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# ── search helpers ────────────────────────────────────────────────────────────

# Words that carry no search signal — stripped before scoring.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "in", "on", "of", "to",
    "under", "size", "i", "am", "looking", "want", "need", "my", "some",
    "that", "this", "is", "are", "it", "me", "find", "show",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase a string into meaningful word tokens (drops stopwords/short words)."""
    if not text:
        return []
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS]


def _size_matches(size: str, listing_size: str) -> bool:
    """
    Fuzzy, case-insensitive size match. The dataset uses messy sizes
    ("S/M", "W30 L30", "US 8.5"), so we check whether the requested size
    appears as a substring of the listing's size string.
    """
    if not listing_size:
        return False
    return size.strip().lower() in listing_size.lower()


def _score_listing(item: dict, keywords: list[str]) -> int:
    """
    Score a listing by keyword overlap with the user's description.
    Matches in the title or style tags are weighted more heavily than
    matches in the free-text description or other fields.
    """
    title_tokens = set(_tokenize(item.get("title", "")))
    tag_tokens = {t.lower() for t in item.get("style_tags", [])}
    desc_tokens = set(_tokenize(item.get("description", "")))
    other_tokens = set(_tokenize(item.get("category", "")))
    other_tokens |= {c.lower() for c in item.get("colors", [])}
    if item.get("brand"):
        other_tokens |= set(_tokenize(item["brand"]))

    score = 0
    for kw in keywords:
        if kw in title_tokens or kw in tag_tokens:
            score += 3
        elif kw in desc_tokens:
            score += 1
        elif kw in other_tokens:
            score += 1
    return score


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Returns a list of matching listing dicts sorted by relevance (best first),
    or an empty list if nothing matches. Never raises.
    """
    listings = load_listings()

    # 1–2. Filter by price ceiling and (fuzzy) size.
    candidates = []
    for item in listings:
        if max_price is not None and item.get("price", 0) > max_price:
            continue
        if size is not None and not _size_matches(size, item.get("size", "")):
            continue
        candidates.append(item)

    # 3–4. Score by keyword overlap and drop zero-relevance listings.
    keywords = _tokenize(description)
    scored = []
    for item in candidates:
        # With no usable keywords, every price/size-passing item is relevant.
        score = _score_listing(item, keywords) if keywords else 1
        if score > 0:
            scored.append((score, item))

    # 5. Sort by score, highest first.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# ── LLM tool helpers ──────────────────────────────────────────────────────────

def _format_item(item: dict) -> str:
    """Render a listing dict into a compact line for an LLM prompt."""
    tags = ", ".join(item.get("style_tags", []))
    colors = ", ".join(item.get("colors", []))
    parts = [
        item.get("title", "an item"),
        f"category: {item.get('category', 'unknown')}",
        f"colors: {colors}" if colors else "",
        f"style: {tags}" if tags else "",
    ]
    return " | ".join(p for p in parts if p)


def _fallback_outfit(new_item: dict) -> str:
    """Offline styling tip used when the LLM call fails."""
    tags = ", ".join(new_item.get("style_tags", [])) or "versatile"
    colors = ", ".join(new_item.get("colors", [])) or "neutral"
    title = new_item.get("title", "this piece")
    return (
        f"Style {title} around its {tags} vibe — lean into the {colors} tones and "
        f"pair it with simple, complementary basics to let the piece stand out."
    )


def _template_caption(new_item: dict) -> str:
    """Offline caption used when the LLM call fails."""
    title = new_item.get("title", "this find")
    price = new_item.get("price")
    platform = new_item.get("platform", "secondhand")
    price_str = f"${price:g}" if price is not None else "a steal"
    return f"scored {title} off {platform} for {price_str} ✨ obsessed with this one."


def _template_caption_no_item() -> str:
    """Offline caption when there's no specific item (caption-only / outfit-only)."""
    return "loving this look 💫 thrown together from pieces I already had and it just works."


def _format_wardrobe(items: list[dict]) -> str:
    """Render wardrobe items into prompt lines."""
    lines = []
    for w in items:
        tags = ", ".join(w.get("style_tags", []))
        colors = ", ".join(w.get("colors", []))
        note = w.get("notes", "")
        line = f"- {w.get('name', 'item')} ({w.get('category', '')}; {colors}; {tags})"
        if note:
            line += f" — {note}"
        lines.append(line)
    return "\n".join(lines)


def _fallback_wardrobe_outfit(items: list[dict]) -> str:
    """Offline wardrobe-only outfit tip used when the LLM call fails."""
    names = [w.get("name", "a piece") for w in items[:3]]
    if len(names) >= 2:
        return f"Try pairing your {names[0]} with your {names[1]} for an easy, put-together look."
    return f"Build a look around your {names[0]} with simple, complementary basics."


def _llm_text(prompt: str, temperature: float, max_tokens: int) -> str:
    """Call the Groq LLM and return the stripped text (raises on API error)."""
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(
    new_item: dict | None, wardrobe: dict, preferences: str | None = None
) -> str:
    """
    Suggest 1–2 complete outfits.

    - With a `new_item`: pair it with the user's wardrobe (or give general advice
      if the wardrobe is empty).
    - With `new_item=None` (outfit-only intent): build outfits purely from the
      user's existing wardrobe.

    Handles empty input gracefully and, on an LLM/API error, returns an offline
    fallback tip instead of raising. `preferences` is optional free-text style
    notes that personalize the suggestion when provided.
    """
    items = (wardrobe or {}).get("items", [])
    pref_line = (
        f"\nThe shopper's saved style preferences: {preferences.strip()}\n"
        if preferences and preferences.strip()
        else ""
    )

    # Outfit-only: no new item — style the existing wardrobe itself.
    if new_item is None:
        if not items:
            return (
                "I don't have any wardrobe pieces to work with yet — pick the example "
                "wardrobe or save a style profile, and I'll put outfits together for you."
            )
        prompt = (
            f"Here is the shopper's wardrobe:\n{_format_wardrobe(items)}\n"
            f"{pref_line}\n"
            "Suggest 1–2 complete outfit combinations using only these pieces "
            "(name the pieces). Keep it to a few short, wearable sentences."
        )
        try:
            return _llm_text(prompt, 0.7, 300) or _fallback_wardrobe_outfit(items)
        except Exception:
            return _fallback_wardrobe_outfit(items)

    item_desc = _format_item(new_item)
    if not items:
        prompt = (
            f"A shopper is considering this secondhand item:\n{item_desc}\n"
            f"{pref_line}\n"
            "They don't have a wardrobe on file yet. Give general styling advice: "
            "what kinds of pieces and colors pair well with it, and what overall "
            "vibe it suits. Keep it to 2–3 short sentences, friendly and practical."
        )
    else:
        prompt = (
            f"A shopper is considering this secondhand item:\n{item_desc}\n"
            f"{pref_line}\n"
            f"Here is their existing wardrobe:\n{_format_wardrobe(items)}\n\n"
            "Suggest 1–2 complete outfit combinations that pair the new item with "
            "specific pieces from their wardrobe (name the pieces). Keep it to a few "
            "short sentences, concrete and wearable."
        )

    try:
        return _llm_text(prompt, 0.7, 300) or _fallback_outfit(new_item)
    except Exception:
        return _fallback_outfit(new_item)


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict | None = None) -> str:
    """
    Generate a short, shareable outfit caption.

    - With a `new_item`: mentions the item name, price, and platform.
    - With `new_item=None` (caption-only / outfit-only): captions the look/outfit
      text alone.

    Guards against an empty outfit (returns an error string, never raises).
    Uses a high temperature so captions vary across runs/inputs. On an LLM/API
    error, returns an offline template caption.
    """
    if not outfit or not outfit.strip():
        return "Can't make a fit card without an outfit suggestion."

    if new_item is None:
        prompt = (
            "Write a short, casual Instagram/TikTok-style caption (2–4 sentences) "
            "for this outfit. Sound like a real person, not a product description.\n\n"
            f"Outfit: {outfit}\n\n"
            "Capture the vibe in specific terms and feel free to use an emoji or two."
        )
        try:
            return _llm_text(prompt, 1.0, 180) or _template_caption_no_item()
        except Exception:
            return _template_caption_no_item()

    title = new_item.get("title", "this find")
    price = new_item.get("price")
    platform = new_item.get("platform", "secondhand")
    price_str = f"${price:g}" if price is not None else "a great price"

    prompt = (
        "Write a short, casual Instagram/TikTok-style caption for an outfit post "
        "(2–4 sentences). It should sound like a real person sharing a thrift find, "
        "not a product description.\n\n"
        f"Item: {title}\n"
        f"Price: {price_str}\n"
        f"Platform: {platform}\n"
        f"Outfit: {outfit}\n\n"
        "Mention the item name, price, and platform naturally (once each), capture "
        "the outfit vibe in specific terms, and feel free to use an emoji or two."
    )

    try:
        return _llm_text(prompt, 1.0, 180) or _template_caption(new_item)
    except Exception:
        return _template_caption(new_item)


# ── Tool 4 (stretch): compare_price ───────────────────────────────────────────

def compare_price(item: dict) -> dict:
    """
    Estimate whether a listing's price is fair by comparing it to comparable
    listings in the dataset (same category, with overlapping style tags).

    Pure Python — no LLM call. Returns a dict:
        {
            "verdict": "good deal" | "fair" | "overpriced" | "unknown",
            "item_price": float | None,
            "median_comparable": float | None,
            "sample_size": int,
            "comparables": list[dict],   # [{id, title, price}, ...]
        }

    Failure mode: if fewer than 2 comparable listings exist, returns
    verdict="unknown" rather than guessing or raising.
    """
    item_price = item.get("price")
    category = item.get("category")
    tags = {t.lower() for t in item.get("style_tags", [])}

    listings = load_listings()

    # Prefer comparables that share both category and at least one style tag.
    comps = [
        l for l in listings
        if l.get("id") != item.get("id")
        and l.get("category") == category
        and tags & {t.lower() for t in l.get("style_tags", [])}
    ]
    # Fall back to same-category listings if the tag-overlap set is too small.
    if len(comps) < 2:
        comps = [
            l for l in listings
            if l.get("id") != item.get("id") and l.get("category") == category
        ]

    prices = [l["price"] for l in comps if isinstance(l.get("price"), (int, float))]

    result = {
        "verdict": "unknown",
        "item_price": item_price,
        "median_comparable": None,
        "sample_size": len(prices),
        "comparables": [
            {"id": l["id"], "title": l["title"], "price": l["price"]} for l in comps
        ],
    }

    if len(prices) < 2 or item_price is None:
        return result

    median = statistics.median(prices)
    result["median_comparable"] = round(median, 2)
    if item_price <= median * 0.85:
        result["verdict"] = "good deal"
    elif item_price >= median * 1.15:
        result["verdict"] = "overpriced"
    else:
        result["verdict"] = "fair"
    return result
