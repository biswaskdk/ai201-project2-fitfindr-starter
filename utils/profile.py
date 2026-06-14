"""
utils/profile.py

Cross-session style-profile memory (stretch feature). Persists a returning
user's wardrobe and free-text style preferences to a JSON file so they don't
have to re-describe their wardrobe every session.

Profile shape:
    {
        "wardrobe": {"items": [...]},   # same schema as the example wardrobe
        "preferences": "string"          # free-text style notes
    }
"""

import json
import os

_PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "style_profile.json")


def load_profile() -> dict | None:
    """
    Load the saved style profile.

    Returns the profile dict, or None if no profile exists yet or the file is
    missing/corrupt (callers fall back to the empty wardrobe in that case).
    """
    try:
        with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_profile(wardrobe: dict, preferences: str = "") -> dict:
    """
    Persist a wardrobe + style preferences to disk. Returns the saved profile.
    """
    profile = {
        "wardrobe": wardrobe or {"items": []},
        "preferences": (preferences or "").strip(),
    }
    with open(_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    return profile


def has_profile() -> bool:
    """True if a saved profile exists."""
    return load_profile() is not None
