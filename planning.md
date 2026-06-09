# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

- `search_listings(description, size, max_price)` — required
- `suggest_outfit(new_item, wardrobe)` — required
- `create_fit_card(outfit, new_item)` — required
- `compare_price(item)` — additional (stretch: price comparison)

LLM provider: **Groq** (`GROQ_API_KEY` in `.env`). `suggest_outfit` and `create_fit_card`
are LLM calls; `search_listings` and `compare_price` are pure-Python over the local dataset.

---

## Stretch Features (planned)

> Per the assignment, this section is updated before starting each stretch feature.
> Three are planned for this project:

| Stretch feature | How it's realized | Where specced below |
|-----------------|-------------------|---------------------|
| **Retry with fallback** | If `search_listings` returns nothing, the planning loop re-runs it with the size filter dropped, then the price ceiling dropped, recording the change in `session["adjustments"]` and telling the user what was loosened. | Planning Loop (step 2), Error Handling table |
| **Price comparison tool** | A fourth tool, `compare_price(item)`, judges whether a price is fair vs. comparable listings (same category + overlapping style tags). Called conditionally when the query signals price intent. | Tool 4 spec, Planning Loop (step 4) |
| **Style profile memory** | A returning user's wardrobe/preferences persist across sessions in `data/style_profile.json` via `load_profile()` / `save_profile()`, so they don't re-describe their wardrobe each time. | State Management (Style profile memory) |

*Not implemented:* trend awareness (requires a live public-platform feed; out of scope for this dataset).

---

### Tool 1: search_listings

**What it does:**
Filters the mock listings dataset (40 items, loaded via `load_listings()`) by an optional
size and price ceiling, then scores the survivors by keyword overlap with the user's
description and returns them best-match-first. Pure Python — no LLM call.

**Input parameters:**
- `description` (str): keywords describing the desired item, e.g. `"vintage graphic tee"`.
  Matched (case-insensitive) against each listing's `title`, `description`, and `style_tags`.
- `size` (str | None): size to filter by, e.g. `"M"`. **Fuzzy substring match** because the
  dataset uses messy sizes like `"S/M"`, `"W30 L30"`, `"US 8.5"`, `"One Size / Oversized"`.
  `None` skips size filtering.
- `max_price` (float | None): inclusive price ceiling. `None` skips price filtering.

**What it returns:**
`list[dict]` of matching listings, sorted by relevance score (descending). Each dict has:
`id, title, description, category, style_tags (list), size, condition, price (float),
colors (list), brand, platform`. Listings with a keyword score of 0 are dropped.
Returns `[]` when nothing matches — never raises.

**What happens if it fails or returns nothing:**
Returns `[]`. The planning loop catches the empty result and triggers
**retry-with-fallback** (see Planning Loop): re-run with the size filter removed, then with
the price ceiling removed, telling the user what was loosened. If still empty after all
fallbacks, the agent stops with a helpful message and does **not** call `suggest_outfit`.

---

### Tool 2: suggest_outfit

**What it does:**
Given a thrifted item and the user's wardrobe, asks the LLM (Groq) to propose 1–2 complete
outfit combinations that pair the new item with named pieces the user already owns.

**Input parameters:**
- `new_item` (dict): a listing dict from `search_listings` (the item being considered).
- `wardrobe` (dict): `{"items": [...]}` where each item has `name, category, colors,
  style_tags, notes`. May be empty.

**What it returns:**
A non-empty `str` describing one or more outfits, referencing specific wardrobe pieces by
name when possible.

**What happens if it fails or returns nothing:**
- **Empty wardrobe** (`items == []`): instead of failing, prompt the LLM for *general*
  styling advice for the item (what categories/colors/vibes pair well) so a brand-new user
  still gets value.
- **LLM/API error or empty response**: catch the exception and return a graceful fallback
  string (e.g. a generic styling tip built from the item's `style_tags`/`colors`) rather
  than raising or returning `""`.

---

### Tool 3: create_fit_card

**What it does:**
Turns the chosen item + outfit suggestion into a short, casual, shareable caption — the kind
of thing you'd post with an OOTD/thrift-haul photo. LLM call with **higher temperature** so
output varies per input.

**Input parameters:**
- `outfit` (str): the suggestion string returned by `suggest_outfit`.
- `new_item` (dict): the listing dict (used to mention item name, price, platform once each).

**What it returns:**
A 2–4 sentence `str` caption: casual/authentic tone, mentions item name + price + platform
naturally, captures the outfit vibe, and reads differently for different inputs.

**What happens if it fails or returns nothing:**
- Guard first: if `outfit` is empty/whitespace-only, return a descriptive error **string**
  (e.g. `"Can't make a fit card without an outfit suggestion."`) — never raise.
- On LLM/API error: catch and return a simple template caption built from `new_item`
  fields so the user still gets *something*.

---

### Additional Tools (if any)

### Tool 4: compare_price (stretch — price comparison)

**What it does:**
Estimates whether a listing's price is fair by comparing it to **comparable listings** in the
dataset (same `category`, with overlapping `style_tags`). Pure Python — no LLM call.

**Input parameters:**
- `item` (dict): a listing dict (typically `session["selected_item"]`).

**What it returns:**
A dict, e.g.
`{"verdict": "fair" | "good deal" | "overpriced" | "unknown", "item_price": 22.0,
"median_comparable": 26.5, "sample_size": 6, "comparables": [...]}`.
Verdict rule of thumb: > ~15% below median → "good deal"; within ±15% → "fair";
> ~15% above → "overpriced".

**What happens if it fails or returns nothing:**
If fewer than 2 comparable listings exist, return `{"verdict": "unknown",
"sample_size": <n>}` and the agent tells the user it can't judge the price confidently —
it does not guess or raise.

---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop is **rule-based and conditional** — it reacts to what each tool returns instead of
blindly running a fixed sequence. State lives in the `session` dict.

1. **Parse** the query (see State Management → Parsing) into `{description, size, max_price}`.
2. **Search** with `search_listings`.
   - **If results are empty → retry-with-fallback** (stretch): re-run dropping the `size`
     filter; if still empty, re-run dropping `max_price`; record what was loosened in
     `session["adjustments"]`. *This branch is what makes the loop conditional rather than fixed.*
   - If still empty after all fallbacks → set `session["error"]` with guidance and **stop**
     (do not proceed to outfit/fit-card with empty input).
3. **Select** the top-scored result → `session["selected_item"]`.
4. **(Conditional) compare_price** (stretch): only when the user's query signals price
   intent (mentions price, "deal", "worth it", "fair", or a budget). Result →
   `session["price_verdict"]`. Skipped otherwise.
5. **Suggest outfit** with `suggest_outfit(selected_item, wardrobe)`
   → `session["outfit_suggestion"]`. Empty wardrobe handled inside the tool.
6. **Create fit card** with `create_fit_card(outfit_suggestion, selected_item)`
   → `session["fit_card"]`. Only runs if step 5 produced a non-empty suggestion.
7. **Done** when `fit_card` is set (success) or `error` is set (early stop). Return `session`.

**How it knows it's done:** the loop terminates when either `session["fit_card"]` or
`session["error"]` is populated. There is no infinite loop — fallbacks are bounded (at most
two retries), and each step is gated on the previous step's output.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict (created by `_new_session`) is the source of truth for one
interaction. Each tool's output is written back to it, and later tools read from it — the
user never re-enters anything.

Tracked fields:
- `query` (original text), `parsed` (`{description, size, max_price}`)
- `search_results` (list), `adjustments` (what fallback loosened, if any)
- `selected_item` → flows into `compare_price` **and** `suggest_outfit`
- `wardrobe`, `outfit_suggestion` → flows into `create_fit_card`
- `price_verdict` (from `compare_price`, optional)
- `fit_card`, `error`

**Parsing (hybrid):** regex first — pull `size` via `r"size\s+([\w/.]+)"` and `max_price`
via `r"under\s*\$?(\d+)"`; the remaining words become `description`. **Fallback to LLM**
only when regex finds no usable description/size (ambiguous phrasing): ask Groq to return
JSON `{description, size, max_price}` and validate it. Result stored in `session["parsed"]`.

**Style profile memory (stretch — cross-session state):** a small JSON file (e.g.
`data/style_profile.json`) holds the returning user's wardrobe + preferences.
`load_profile()` reads it at session start (used as the wardrobe if the user doesn't pass
one); `save_profile()` writes updates. This persists *beyond* a single `session` dict, so a
returning user doesn't re-describe their wardrobe. Missing/corrupt file → fall back to the
empty wardrobe and continue.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Return `[]`; loop retries with size dropped, then price dropped, reporting what changed; if still empty, stop with a helpful message and skip downstream tools. |
| suggest_outfit | Wardrobe is empty | Tool detects `items == []` and returns general styling advice for the item instead of failing. |
| suggest_outfit | LLM / API error | Catch exception; return a fallback styling tip built from the item's style_tags/colors. |
| create_fit_card | Outfit input missing or incomplete | Guard for empty/whitespace `outfit`; return a descriptive error string (no exception). |
| create_fit_card | LLM / API error | Catch exception; return a simple template caption from item fields. |
| compare_price | Fewer than 2 comparable listings | Return `verdict="unknown"`; agent tells user it can't judge the price confidently. |
| parse / query | Empty query | `app.py` / loop guards and returns an error message before any tool runs. |
| style profile | Missing or corrupt profile file | Fall back to empty wardrobe; continue the session. |

---

## Architecture

```
                         User query  ──────────────┐
                              │                     │ (returning user)
                              ▼                     ▼
                        Parse query        load_profile()  ◄── data/style_profile.json
                   (regex → LLM fallback)        │  (cross-session memory)
                              │                   │
                              ▼                   ▼
        ┌────────────────  Planning Loop (rule-based, conditional)  ────────────────┐
        │                                                                           │
        │  search_listings(description, size, max_price)                            │
        │        │                                                                  │
        │   results == [] ──► retry: drop size ──► drop price ──► still empty ──► ERROR ──► return
        │        │                 (record session["adjustments"])                  │
        │   results != []                                                           │
        │        ▼                                                                  │
        │   session["selected_item"] = results[0]                                   │
        │        │                                                                  │
        │   price intent in query?  ──yes──► compare_price(item) ► session["price_verdict"]
        │        │ (no → skip)                                                      │
        │        ▼                                                                  │
        │   suggest_outfit(selected_item, wardrobe)  ─[empty wardrobe → general advice]
        │        │                          ─[API error → fallback tip]             │
        │   session["outfit_suggestion"]                                            │
        │        │ (only if non-empty)                                              │
        │        ▼                                                                  │
        │   create_fit_card(outfit_suggestion, selected_item) ─[empty → error str]  │
        │        │                                            ─[API error → template]│
        │   session["fit_card"]                                                     │
        └────────────────────────────────────┬──────────────────────────────────── ┘
                                              ▼
                            save_profile()  (persist wardrobe/prefs)
                                              ▼
                                  Return session  →  Gradio UI (app.py)
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
- **Tool used:** Claude (Claude Code), with this planning.md open.
- **Input I'll give it:** the Tool 1–4 specs above (inputs / return shape / failure mode),
  plus `load_listings()` from `utils/data_loader.py` and the listing field list.
- **What I expect it to produce:** `search_listings` and `compare_price` as pure-Python
  functions; `suggest_outfit` and `create_fit_card` as Groq calls following the prompt
  guidelines (general-advice branch for empty wardrobe; higher temperature for fit cards).
- **How I'll verify before trusting it:** run each tool in isolation —
  - `search_listings`: 3 queries → a normal hit, a size-filtered hit, and a guaranteed miss
    (`"designer ballgown size XXS under $5"`) returns `[]`.
  - `suggest_outfit`: once with `get_example_wardrobe()`, once with `get_empty_wardrobe()`.
  - `create_fit_card`: same item twice → confirm captions differ; empty `outfit` → error string.
  - `compare_price`: a clearly cheap item, a clearly expensive item, and a category with <2 comparables.

**Milestone 4 — Planning loop and state management:**
- **Tool used:** Claude, given the Planning Loop + State Management sections and the diagram.
- **What I expect it to produce:** `run_agent()` in `agent.py` implementing the conditional
  loop (parse → search → fallback retries → optional compare_price → suggest → fit card),
  writing every result into the `session` dict; plus `handle_query()` in `app.py` mapping
  the session to the three UI panels and guarding empty queries.
- **How I'll verify:** run the happy path (`vintage graphic tee under $30`) end-to-end and
  confirm all three panels populate; run the no-results query and confirm the fallback
  message appears and downstream tools are skipped; toggle the empty-wardrobe radio and
  confirm general advice is returned.

---

## A Complete Interaction (Step by Step)

Write out what a full user interaction looks like from start to finish — tool call by tool call. Use a specific example query.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 0 — Parse:** regex extracts `max_price=30.0`; no explicit `size` → left `None`;
`description="vintage graphic tee"`. Stored in `session["parsed"]`.

**Step 1 — Search:** `search_listings("vintage graphic tee", size=None, max_price=30.0)`
returns matching listings sorted by relevance. FitFindr selects the top result
(e.g. *"Faded Band Tee — $22, Depop, good condition"*) → `session["selected_item"]`.

**Step 2 — (Conditional) compare_price:** the query mentions price ("under $30"), so
`compare_price(selected_item)` runs and finds the $22 tee sits below the median of
comparable tees → `session["price_verdict"] = "good deal"`.

**Step 3 — Suggest outfit:** `suggest_outfit(new_item=<band tee>, wardrobe=<user's>)`
returns e.g. *"Pair this with your baggy dark-wash jeans and chunky sneakers for a 90s
streetwear look — tuck the front corner for shape."* → `session["outfit_suggestion"]`.

**Step 4 — Fit card:** `create_fit_card(outfit=<suggestion>, new_item=<band tee>)` returns
e.g. *"thrifted this faded band tee off depop for $22 and it was made for my baggy jeans 🖤
full look in my stories"* → `session["fit_card"]`.

**Error path:** if `search_listings` returns nothing, the loop retries with size then price
removed (reporting the adjustment); if still empty it stops with a helpful message and does
**not** call `suggest_outfit` with empty input.

**Final output to user:** the Gradio UI shows three panels — the top listing (title, price,
platform, condition, plus the price verdict), the outfit idea, and the shareable fit card.

---

### Reference: dataset & schema fields

- `data/listings.json` (40 items) fields: `id, title, description, category` (tops, bottoms,
  outerwear, shoes, accessories), `style_tags` (list), `size`, `condition`
  (excellent/good/fair), `price` (float), `colors` (list), `brand`, `platform`
  (depop/thredUp/poshmark). **Sizes are inconsistent** (`"S/M"`, `"W30 L30"`, `"US 8.5"`) —
  filter by fuzzy substring.
- `data/wardrobe_schema.json` item fields: `id, name, category, colors, style_tags, notes`.
- `utils/data_loader.py` provides `load_listings()`, `get_example_wardrobe()`,
  `get_empty_wardrobe()` — use these rather than re-reading files.
```
