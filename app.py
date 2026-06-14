"""
app.py

Gradio interface for FitFindr. handle_query() calls run_agent() and maps the
session results onto the three output panels. Also supports a remembered style
profile (stretch feature) so a returning user doesn't re-describe their wardrobe.

Run with:
    python app.py

Then open the localhost URL shown in your terminal (usually http://localhost:7860,
but check your terminal — the port may differ).
"""

import gradio as gr

from agent import run_agent
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe
from utils.profile import load_profile, save_profile

SAVED_PROFILE = "Saved profile (remembered)"


# ── wardrobe resolution ───────────────────────────────────────────────────────

def _resolve_wardrobe(wardrobe_choice: str, preferences: str) -> tuple[dict, str]:
    """
    Map the radio choice to a wardrobe dict and effective preferences.
    For the saved profile, falls back to the empty wardrobe if none is stored,
    and uses the saved preferences when the box is left blank.
    """
    if wardrobe_choice == SAVED_PROFILE:
        profile = load_profile()
        if profile:
            wardrobe = profile.get("wardrobe") or get_empty_wardrobe()
            if not (preferences or "").strip():
                preferences = profile.get("preferences", "")
            return wardrobe, preferences
        return get_empty_wardrobe(), preferences
    if wardrobe_choice == "Empty wardrobe (new user)":
        return get_empty_wardrobe(), preferences
    return get_example_wardrobe(), preferences


# ── query handler ─────────────────────────────────────────────────────────────

def handle_query(
    user_query: str, wardrobe_choice: str, preferences: str
) -> tuple[str, str, str]:
    """
    Called by Gradio when the user submits a query. Returns three strings,
    one per output panel: (listing_text, outfit_suggestion, fit_card).
    """
    if not user_query or not user_query.strip():
        return "Please describe what you're looking for.", "", ""

    wardrobe, effective_prefs = _resolve_wardrobe(wardrobe_choice, preferences)

    session = run_agent(user_query, wardrobe, preferences=effective_prefs)

    # Error / no-results path → message in the first panel, others blank.
    if session["error"]:
        return session["error"], "", ""

    item = session["selected_item"]
    parts = []

    if item:
        # Stretch (retry-with-fallback): tell the user if filters were loosened.
        if session["adjustments"]:
            parts.append("ℹ️ No exact match — I " + ", and ".join(session["adjustments"]) + ".")
        parts.append(
            f"{item['title']}\n"
            f"${item['price']:g} · {item['platform']} · {item['condition']} condition\n"
            f"Size: {item['size']}\n"
            f"Brand: {item.get('brand') or 'Unbranded'}\n\n"
            f"{item['description']}"
        )
        # Stretch (price comparison): show the verdict when it ran.
        pv = session["price_verdict"]
        if pv:
            if pv["verdict"] == "unknown":
                parts.append("💰 Price check: not enough comparable listings to judge.")
            else:
                parts.append(
                    f"💰 Price check: {pv['verdict']} — ${pv['item_price']:g} vs "
                    f"${pv['median_comparable']:g} median of {pv['sample_size']} similar items."
                )
    elif session["intent"] == "outfit_only":
        parts.append("👗 Styling your existing wardrobe — no new listing searched for this request.")
    elif session["intent"] == "caption_only":
        parts.append("✨ Caption generated from the look you described — no listing searched.")

    listing_text = "\n\n".join(parts)
    return listing_text, session["outfit_suggestion"] or "", session["fit_card"] or ""


# ── save profile handler ──────────────────────────────────────────────────────

def save_my_profile(wardrobe_choice: str, preferences: str) -> str:
    """Persist the chosen wardrobe + preferences as the remembered style profile."""
    if wardrobe_choice == SAVED_PROFILE:
        existing = load_profile()
        wardrobe = (existing or {}).get("wardrobe") or get_empty_wardrobe()
    elif wardrobe_choice == "Empty wardrobe (new user)":
        wardrobe = get_empty_wardrobe()
    else:
        wardrobe = get_example_wardrobe()

    save_profile(wardrobe, preferences)
    n = len(wardrobe.get("items", []))
    return f"✅ Saved your style profile ({n} wardrobe items + preferences). Select \"{SAVED_PROFILE}\" to use it next time."


# ── interface ─────────────────────────────────────────────────────────────────

EXAMPLE_QUERIES = [
    "vintage graphic tee under $30",
    "90s track jacket in size M",
    "flowy midi skirt under $40",
    "is a pair of black combat boots size 8 a good deal?",
    "designer ballgown size XXS under $5",   # deliberate no-results test
]

def build_interface():
    with gr.Blocks(title="FitFindr") as demo:
        gr.Markdown("""
# FitFindr 🛍️
Find secondhand pieces and get outfit ideas based on your wardrobe.
Describe what you're looking for — include size and price if you want to filter.
        """)

        with gr.Row():
            query_input = gr.Textbox(
                label="What are you looking for?",
                placeholder="e.g. vintage graphic tee under $30, size M",
                lines=2,
                scale=3,
            )
            wardrobe_choice = gr.Radio(
                choices=[
                    "Example wardrobe",
                    "Empty wardrobe (new user)",
                    SAVED_PROFILE,
                ],
                value="Example wardrobe",
                label="Wardrobe",
                scale=1,
            )

        with gr.Row():
            preferences_input = gr.Textbox(
                label="Style preferences (optional — remembered across sessions)",
                placeholder="e.g. I love 90s grunge, avoid bright colors, prefer baggy fits",
                lines=1,
                scale=3,
            )
            save_btn = gr.Button("💾 Remember my style", scale=1)

        save_status = gr.Markdown("")

        submit_btn = gr.Button("Find it", variant="primary")

        with gr.Row():
            listing_output = gr.Textbox(
                label="🛍️ Top listing found",
                lines=10,
                interactive=False,
            )
            outfit_output = gr.Textbox(
                label="👗 Outfit idea",
                lines=10,
                interactive=False,
            )
            fitcard_output = gr.Textbox(
                label="✨ Your fit card",
                lines=10,
                interactive=False,
            )

        gr.Examples(
            examples=[[q, "Example wardrobe"] for q in EXAMPLE_QUERIES],
            inputs=[query_input, wardrobe_choice],
            label="Try these queries",
        )

        query_inputs = [query_input, wardrobe_choice, preferences_input]
        outputs = [listing_output, outfit_output, fitcard_output]

        submit_btn.click(fn=handle_query, inputs=query_inputs, outputs=outputs)
        query_input.submit(fn=handle_query, inputs=query_inputs, outputs=outputs)
        save_btn.click(
            fn=save_my_profile,
            inputs=[wardrobe_choice, preferences_input],
            outputs=save_status,
        )

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch()
