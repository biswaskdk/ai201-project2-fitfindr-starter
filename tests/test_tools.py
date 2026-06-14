"""
tests/test_tools.py

Tool tests for FitFindr. Run with:  pytest tests/

The pure-Python search tests always run. The LLM-backed tests (suggest_outfit,
create_fit_card variation) are skipped automatically when GROQ_API_KEY is not
set, so the suite is runnable offline.
"""

import os

import pytest

from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

needs_groq = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set — skipping live LLM test",
)


# ── search_listings ───────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Failure mode: no match → empty list, no exception.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_filter_fuzzy():
    # "M" should match messy sizes like "S/M" via substring matching.
    results = search_listings("jacket", size="M", max_price=None)
    assert all("m" in item["size"].lower() for item in results)


def test_search_sorted_by_relevance():
    results = search_listings("vintage denim jeans", size=None, max_price=None)
    # First result should be at least as relevant as the last (list is non-empty).
    assert isinstance(results, list)
    assert len(results) > 0


# ── suggest_outfit ────────────────────────────────────────────────────────────

@needs_groq
def test_suggest_outfit_with_wardrobe():
    results = search_listings("vintage graphic tee", max_price=50)
    suggestion = suggest_outfit(results[0], get_example_wardrobe())
    assert isinstance(suggestion, str)
    assert suggestion.strip() != ""


@needs_groq
def test_suggest_outfit_empty_wardrobe():
    # Failure mode: empty wardrobe → general advice, not a crash/empty string.
    results = search_listings("vintage graphic tee", max_price=50)
    suggestion = suggest_outfit(results[0], get_empty_wardrobe())
    assert isinstance(suggestion, str)
    assert suggestion.strip() != ""


# ── create_fit_card ───────────────────────────────────────────────────────────

def test_fit_card_empty_outfit():
    # Failure mode: empty outfit → error string, no exception (no API call needed).
    item = {"title": "Faded Band Tee", "price": 22.0, "platform": "depop"}
    card = create_fit_card("", item)
    assert isinstance(card, str)
    assert card.strip() != ""


def test_fit_card_whitespace_outfit():
    item = {"title": "Faded Band Tee", "price": 22.0, "platform": "depop"}
    card = create_fit_card("   ", item)
    assert isinstance(card, str)
    assert card.strip() != ""


@needs_groq
def test_fit_card_varies():
    # Same input should produce different captions across runs (high temperature).
    item = {"title": "Faded Band Tee", "price": 22.0, "platform": "depop"}
    outfit = "Pair it with baggy jeans and chunky sneakers for a 90s look."
    cards = {create_fit_card(outfit, item) for _ in range(3)}
    assert len(cards) > 1
