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


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Handles an empty wardrobe by giving general styling advice. On an LLM/API
    error, returns an offline fallback tip instead of raising.
    """
    items = (wardrobe or {}).get("items", [])
    item_desc = _format_item(new_item)

    if not items:
        prompt = (
            f"A shopper is considering this secondhand item:\n{item_desc}\n\n"
            "They don't have a wardrobe on file yet. Give general styling advice: "
            "what kinds of pieces and colors pair well with it, and what overall "
            "vibe it suits. Keep it to 2–3 short sentences, friendly and practical."
        )
    else:
        wardrobe_lines = []
        for w in items:
            tags = ", ".join(w.get("style_tags", []))
            colors = ", ".join(w.get("colors", []))
            note = w.get("notes", "")
            line = f"- {w.get('name', 'item')} ({w.get('category', '')}; {colors}; {tags})"
            if note:
                line += f" — {note}"
            wardrobe_lines.append(line)
        wardrobe_text = "\n".join(wardrobe_lines)
        prompt = (
            f"A shopper is considering this secondhand item:\n{item_desc}\n\n"
            f"Here is their existing wardrobe:\n{wardrobe_text}\n\n"
            "Suggest 1–2 complete outfit combinations that pair the new item with "
            "specific pieces from their wardrobe (name the pieces). Keep it to a few "
            "short sentences, concrete and wearable."
        )

    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=300,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text if text else _fallback_outfit(new_item)
    except Exception:
        return _fallback_outfit(new_item)


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Guards against an empty outfit (returns an error string, never raises).
    Uses a high temperature so captions vary across runs/inputs. On an LLM/API
    error, returns an offline template caption.
    """
    if not outfit or not outfit.strip():
        return "Can't make a fit card without an outfit suggestion."

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
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=1.0,
            max_tokens=180,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text if text else _template_caption(new_item)
    except Exception:
        return _template_caption(new_item)
