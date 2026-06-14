"""
tests/test_agent.py

Tests for the planning loop's intent routing. The keyword-driven classification
cases run offline; the full end-to-end runs are skipped without GROQ_API_KEY.
"""

import os

import pytest

from agent import _classify_intent, run_agent
from utils.data_loader import get_example_wardrobe

needs_groq = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set — skipping live LLM test",
)


# ── intent classification (strong keyword signals, offline) ────────────────────

@pytest.mark.parametrize("query,expected", [
    ("find me a vintage graphic tee under $30", "full"),
    ("show me black combat boots in size 8", "full"),
    ("give me an outfit idea for today", "outfit_only"),
    ("how would i wear my baggy jeans", "outfit_only"),
    ("is a vintage tee a good deal", "price_check"),
    ("is this overpriced", "price_check"),
    ("write a caption for my grunge look", "caption_only"),
])
def test_classify_intent_keywords(query, expected):
    assert _classify_intent(query) == expected


# ── end-to-end routing: the right tools run for each intent ────────────────────

@needs_groq
def test_full_flow_runs_all_tools():
    s = run_agent("find me a vintage graphic tee under $30", get_example_wardrobe())
    assert s["intent"] == "full"
    assert s["searched"] and s["selected_item"]
    assert s["outfit_suggestion"] and s["fit_card"]


@needs_groq
def test_outfit_only_skips_search():
    s = run_agent("give me an outfit idea for today", get_example_wardrobe())
    assert s["intent"] == "outfit_only"
    assert s["searched"] is False
    assert s["selected_item"] is None
    assert s["outfit_suggestion"] and s["fit_card"]


@needs_groq
def test_price_check_skips_styling():
    s = run_agent("is a vintage graphic tee a good deal?", get_example_wardrobe())
    assert s["intent"] == "price_check"
    assert s["price_verdict"] is not None
    assert s["outfit_suggestion"] is None
    assert s["fit_card"] is None


@needs_groq
def test_caption_only_only_captions():
    s = run_agent("write a caption for my band tee and baggy jeans look", get_example_wardrobe())
    assert s["intent"] == "caption_only"
    assert s["searched"] is False
    assert s["outfit_suggestion"] is None
    assert s["fit_card"]
