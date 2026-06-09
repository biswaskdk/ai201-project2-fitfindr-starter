# FitFindr 🛍️

A multi-tool AI agent that helps you find secondhand clothing and figure out how to wear it.
You describe what you're after in plain language; FitFindr searches a mock listings dataset,
checks whether the price is fair, styles the piece against your wardrobe, and writes a
shareable "fit card" caption — handling empty results and failures along the way.

---

## Setup

```bash
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file in the project root (free key at
[console.groq.com](https://console.groq.com)):

```
GROQ_API_KEY=your_key_here
```

## Running it

**Web UI (recommended):**
```bash
python app.py
```
Then open the localhost URL printed in your terminal (usually http://localhost:7860).

**Command line (quick test of the agent):**
```bash
python agent.py
```

**Verify the data loads:**
```bash
python utils/data_loader.py
```

---

## Tools

FitFindr orchestrates four tools. The first three are required; `compare_price` is a stretch feature.

| Tool | Signature | What it does | Type |
|------|-----------|--------------|------|
| `search_listings` | `(description, size, max_price) -> list[dict]` | Filters the 40-item listings dataset by size and price, scores survivors by keyword overlap with the description, returns best matches first (or `[]`). | Pure Python |
| `suggest_outfit` | `(new_item, wardrobe) -> str` | Suggests 1–2 complete outfits pairing the item with pieces in the user's wardrobe; gives general advice if the wardrobe is empty. | Groq LLM |
| `create_fit_card` | `(outfit, new_item) -> str` | Writes a short, casual, shareable caption for the look — different each time. | Groq LLM (higher temp) |
| `compare_price` *(stretch)* | `(item) -> dict` | Estimates whether the price is fair vs. comparable listings (same category + overlapping style tags). | Pure Python |

Full input/return specs for each tool are in [`planning.md`](planning.md).

---

## How the planning loop works

`run_agent(query, wardrobe)` in [`agent.py`](agent.py) drives a **rule-based conditional
loop**. It is not a fixed sequence — on each iteration it inspects the current session state
and decides what to do next based on what previous tools returned. The decision logic
(`decide_next_step`) chooses among these actions:

1. **Parse** the query into `{description, size, max_price}` (hybrid: regex first, with an
   LLM fallback for ambiguous phrasing).
2. **Search** with `search_listings`.
   - **If results are empty → retry with loosened constraints** (drop the size filter, then
     the price ceiling), recording what was adjusted so it can tell the user. *This branch is
     what makes the loop conditional rather than linear.*
   - If still empty after all fallbacks → stop with a helpful message; the agent does **not**
     proceed to outfit/fit-card with empty input.
3. **Select** the top-scored result as `selected_item`.
4. **(Conditional) compare_price** — runs **only** when the query signals price intent
   (mentions price, "deal", "worth it", "fair", or a budget). Skipped otherwise.
5. **Suggest outfit** from the selected item + wardrobe.
6. **Create fit card** from the outfit suggestion — only if step 5 produced a real suggestion.

The loop terminates when a fit card has been produced (success) or an error has been set
(early stop). Fallback retries are bounded, so the loop always terminates.

---

## State management

A single `session` dict (created by `_new_session`) is the source of truth for one
interaction. Each tool writes its output back to the session, and later tools read from it —
**the user never re-enters anything**. For example, the item chosen by `search_listings` is
stored as `session["selected_item"]` and flows into both `compare_price` and
`suggest_outfit`; the outfit string flows into `create_fit_card`.

Tracked fields: `query`, `parsed`, `search_results`, `adjustments` (what fallback loosened),
`selected_item`, `wardrobe`, `price_verdict`, `outfit_suggestion`, `fit_card`, and `error`.

---

## Error handling strategy

Every tool handles its own failure mode — nothing fails silently and nothing crashes the agent.

| Tool | Failure mode | Response |
|------|-------------|----------|
| `search_listings` | No results match | Returns `[]`; loop retries with size dropped, then price dropped, and reports what changed. If still empty, stops with guidance and skips downstream tools. |
| `suggest_outfit` | Empty wardrobe | Returns general styling advice for the item instead of failing. |
| `suggest_outfit` | LLM / API error | Caught; returns a fallback tip built from the item's style tags/colors. |
| `create_fit_card` | Missing/empty outfit | Guards and returns a descriptive error string (no exception). |
| `create_fit_card` | LLM / API error | Caught; returns a simple template caption from item fields. |
| `compare_price` | Fewer than 2 comparables | Returns `verdict="unknown"`; agent says it can't judge the price confidently. |
| query | Empty query | Guarded in `app.py` / loop before any tool runs. |

The guiding rule: on empty results or errors, the agent **communicates the problem to the
user** and either **falls back** (loosen the search, give general advice, use a template) or
**asks for / waits on more input** — never a silent failure or a crash.

---

## Stretch features implemented

- **Retry with fallback** — `search_listings` auto-loosens constraints (size, then price)
  when nothing matches, and tells the user what it adjusted.
- **Price comparison tool** — `compare_price` judges whether a find is a good deal against
  comparable listings in the dataset.
- **Style profile memory** — a returning user's wardrobe/preferences persist across sessions
  in `data/style_profile.json` (`load_profile()` / `save_profile()`), so they don't have to
  re-describe their wardrobe. A missing or corrupt file falls back to an empty wardrobe.

---

## Project structure

```
ai201-project2-fitfindr-starter/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe format + example/empty wardrobes
├── utils/
│   └── data_loader.py         # load_listings(), get_example_wardrobe(), get_empty_wardrobe()
├── tools.py                   # The four tools
├── agent.py                   # Planning loop (run_agent) + session state
├── app.py                     # Gradio web interface
├── planning.md                # Design spec (tools, loop, state, error handling)
└── requirements.txt
```

### The dataset

`data/listings.json` has 40 listings across tops, bottoms, outerwear, shoes, and accessories.
Each has: `id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`,
`price`, `colors`, `brand`, `platform` (depop / thredUp / poshmark). Sizes are intentionally
inconsistent (`"S/M"`, `"W30 L30"`, `"US 8.5"`), so `search_listings` matches size by fuzzy
substring rather than exact equality.

`data/wardrobe_schema.json` defines wardrobe items (`id`, `name`, `category`, `colors`,
`style_tags`, `notes`) and includes an example wardrobe and an empty template.

---

## Example interaction

**Query:** *"I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and
chunky sneakers — what's out there and how would I style it?"*

1. **Parse** → `description="vintage graphic tee"`, `max_price=30.0`, `size=None`.
2. **Search** → returns matching tees; top result selected (e.g. *Faded Band Tee — $22, Depop*).
3. **Compare price** (query mentions price) → $22 is below the median for comparable tees → *good deal*.
4. **Suggest outfit** → *"Pair it with your baggy dark-wash jeans and chunky sneakers for a 90s streetwear look…"*
5. **Fit card** → *"thrifted this faded band tee off depop for $22 and it was made for my baggy jeans 🖤"*

If the search had returned nothing, the agent would have retried with the size/price filters
removed (telling you what it loosened) and, if still empty, stopped with a helpful message
rather than calling the styling tools on empty input.
```
