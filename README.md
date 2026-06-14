# FitFindr 🛍️

A multi-tool AI agent that helps you find secondhand clothing and figure out how to wear it.
You describe what you're after in plain language; FitFindr searches a mock listings dataset,
styles the piece against your wardrobe, and writes a shareable "fit card" caption — handling
empty results and tool failures gracefully along the way.

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
Open the localhost URL printed in your terminal (usually http://localhost:7860 — check the
output, the port can differ). Enter a query, pick a wardrobe, and click **Find it**; the three
panels show the top listing, an outfit idea, and your fit card.

**Command line (agent happy-path + no-results branch):**
```bash
python agent.py
```

**Run the tests:**
```bash
pytest tests/
```

---

## Tool inventory

FitFindr uses three tools. `search_listings` is pure Python over the local dataset; the other
two call the Groq LLM (`llama-3.3-70b-versatile`).

### `search_listings(description, size, max_price) -> list[dict]`
- **Purpose:** Find listings matching the user's request.
- **Inputs:**
  - `description` (`str`) — keywords describing the item (e.g. `"vintage graphic tee"`).
  - `size` (`str | None`) — size to filter by; `None` skips the size filter.
  - `max_price` (`float | None`) — inclusive price ceiling; `None` skips the price filter.
- **Output:** `list[dict]` of matching listings sorted by relevance (best first); `[]` if
  nothing matches. Each listing dict has `id, title, description, category, style_tags,
  size, condition, price, colors, brand, platform`.
- **How it ranks:** filters by price and (fuzzy, substring) size, then scores survivors by
  keyword overlap with `description` — matches in the title or style tags count 3×, matches
  in the description or other fields count 1×. Zero-score listings are dropped.

### `suggest_outfit(new_item, wardrobe) -> str`
- **Purpose:** Suggest 1–2 complete outfits pairing the found item with the user's wardrobe.
- **Inputs:**
  - `new_item` (`dict`) — a listing dict (the item being considered).
  - `wardrobe` (`dict`) — `{"items": [...]}`, each item with `name, category, colors,
    style_tags, notes`. May be empty.
- **Output:** `str` — outfit suggestions naming specific wardrobe pieces, or general styling
  advice if the wardrobe is empty.

### `create_fit_card(outfit, new_item) -> str`
- **Purpose:** Write a short, casual, shareable caption for the look.
- **Inputs:**
  - `outfit` (`str`) — the suggestion string from `suggest_outfit`.
  - `new_item` (`dict`) — the listing dict (for item name, price, platform).
- **Output:** `str` — a 2–4 sentence caption. Uses a high LLM temperature (`1.0`) so captions
  vary across runs and inputs.

### `compare_price(item) -> dict`  *(stretch)*
- **Purpose:** Estimate whether a listing's price is fair vs. comparable listings.
- **Inputs:**
  - `item` (`dict`) — a listing dict (typically the selected item).
- **Output:** `dict` — `{verdict, item_price, median_comparable, sample_size, comparables}`,
  where `verdict` is `"good deal" | "fair" | "overpriced" | "unknown"`. Comparables are
  same-category listings with overlapping style tags (falling back to same-category only);
  with fewer than 2 comparables the verdict is `"unknown"`. Pure Python — no LLM call.

---

## How the planning loop works

`run_agent(query, wardrobe)` in [`agent.py`](agent.py) is a **conditional planning loop**, not
a fixed sequence. It first **classifies the user's intent**, then runs only the tools that
intent needs. Each iteration calls `_decide_next_step(session)`, which inspects the current
state (and the intent) and returns the next action.

**Step 1 — intent routing (hybrid).** `_classify_intent(query)` decides what the user wants.
Strong keyword signals resolve directly; ambiguous or conflicting queries fall back to an LLM
classifier (`_llm_classify_intent`). The four intents and the tools they run:

| Intent | Triggered by (examples) | Tools run |
|--------|-------------------------|-----------|
| `full` | "find me a vintage tee under $30" | search → [compare if price] → suggest → fitcard |
| `outfit_only` | "give me an outfit idea", "how would I wear my jeans" | suggest (from wardrobe) → fitcard |
| `price_check` | "is this a good deal?", "is it overpriced?" | search → compare |
| `caption_only` | "write a caption for my grunge look" | fitcard (from the described look) |

So the agent does genuinely different work per request — an outfit-only ask **never calls
`search_listings`**; a price check **never calls the styling tools**.

**Step 2 — execute the intent's plan.** Within an intent, `_decide_next_step` still branches on
results:
- **`handle_empty`** — for search-based intents, if `search_listings` returns `[]` (after
  fallbacks), the loop stops with a helpful message and **never calls the styling tools on
  empty input**.
- **retry-with-fallback** — on an empty search it loosens the size filter, then the price
  ceiling, re-searching each time and recording what it changed.
- **`compare`** — runs for `price_check`, and also in `full` when the query mentions price.

The loop always terminates (`done` or `handle_empty`; each loosened filter is set to `None`, so
fallbacks are bounded). For the no-search intents, `suggest_outfit` accepts `new_item=None`
(styling the wardrobe itself) and `create_fit_card` accepts `new_item=None` (captioning a
described look).

**Query parsing is hybrid.** Regex extracts the size (`size M`) and price ceiling
(`under $30`, `$30`), and the leftover words become the description. If regex strips away
everything meaningful, it falls back to asking the LLM to parse the query into
`{description, size, max_price}`.

---

## State management

A single `session` dict (created by `_new_session`) is the source of truth for one
interaction. Each tool writes its result back into the session, and later tools read from it —
**the user never re-enters anything**. The item chosen by `search_listings` is stored as
`session["selected_item"]` and that exact dict is passed into `suggest_outfit`; the returned
string is stored as `session["outfit_suggestion"]` and passed verbatim into `create_fit_card`.

Fields: `query`, `parsed`, `searched`, `search_results`, `selected_item`, `wardrobe`,
`outfit_suggestion`, `fit_card`, `error`. `app.py`'s `handle_query()` reads the finished
session and maps `selected_item` / `outfit_suggestion` / `fit_card` onto the three UI panels
(or routes `error` to the first panel).

---

## Error handling

Every tool handles its own failure mode — nothing fails silently and nothing crashes the agent.

| Tool | Failure mode | Response |
|------|-------------|----------|
| `search_listings` | No results match | Returns `[]` (never raises). The loop routes to `handle_empty`, reports what was searched, and skips the styling tools. |
| `suggest_outfit` | Empty wardrobe | Returns general styling advice for the item instead of failing. |
| `suggest_outfit` | LLM / API error | Caught; returns an offline fallback tip built from the item's style tags/colors. |
| `create_fit_card` | Empty / whitespace outfit | Returns a descriptive error string (no exception). |
| `create_fit_card` | LLM / API error | Caught; returns an offline template caption from the item fields. |
| `compare_price` | Fewer than 2 comparable listings | Returns `verdict="unknown"`; the UI says it can't judge the price confidently. |
| style profile | Missing / corrupt profile file | `load_profile()` returns `None`; callers fall back to the empty wardrobe. |
| query | Empty query | Guarded in both `run_agent` and `handle_query` before any tool runs. |

**Concrete examples (from deliberate testing):**

```text
# search_listings — no matches
>>> search_listings('designer ballgown', size='XXS', max_price=5)
[]

# full agent — impossible query (graceful, specific message; no crash)
>>> run_agent('designer ballgown size XXS under $5', get_example_wardrobe())["error"]
'No listings matched "designer ballgown", size XXS, under $5. Try broader keywords,
 or remove the size or price filter.'
# ...and session["fit_card"] / session["outfit_suggestion"] stay None — the styling
# tools are never called on empty input.

# create_fit_card — empty outfit
>>> create_fit_card('', item)
"Can't make a fit card without an outfit suggestion."

# suggest_outfit — empty wardrobe (returns useful advice, not an error)
>>> suggest_outfit(item, get_empty_wardrobe())
'This graphic tee is perfect for adding a vintage touch... pairs well with distressed
 denim, leather jackets, and sneakers...'
```

These are reproducible via the commands in Milestone 5 of the assignment.

---

## Spec reflection

What changed between [`planning.md`](planning.md) and the implementation:

- **Fuzzy size matching was necessary.** The dataset uses inconsistent sizes (`"S/M"`,
  `"W30 L30"`, `"US 8.5"`), so exact equality would match almost nothing. The plan called for
  substring matching, and that proved essential in practice.
- **The loop was built around an explicit `_decide_next_step` dispatcher** rather than a
  straight line of `if` guards, so the conditionality lives in one readable place — this made
  the no-results branch easy to verify.
- **Offline fallbacks were added** to the two LLM tools (a styling tip and a template caption)
  so an API outage degrades gracefully instead of surfacing a stack trace.
- **All three planned stretch features are implemented** (see the next section).
- **Known data issue:** several listing titles in `data/listings.json` contain mojibake
  (`â€"` instead of `—`) from the source encoding. It doesn't affect filtering but shows up in
  raw title display.

---

## Stretch features

All three planned stretch features are implemented:

1. **Retry with fallback** — when `search_listings` returns nothing, the planning loop
   automatically re-searches with the size filter dropped, then the price ceiling dropped,
   and tells the user what it loosened (e.g. *"No exact match — I removed the size filter."*).
2. **Price comparison tool (`compare_price`)** — judges whether the selected item is a good
   deal against comparable listings (same category + overlapping style tags). Runs
   conditionally, only when the query signals price intent, and the verdict is shown in the
   listing panel.
3. **Style profile memory** — `utils/profile.py` persists a wardrobe + free-text style
   preferences to `data/style_profile.json` across sessions. In the UI, **💾 Remember my
   style** saves the profile, and choosing **"Saved profile (remembered)"** reuses it so the
   user doesn't re-describe their wardrobe. Preferences are passed into `suggest_outfit` to
   personalize suggestions. A missing/corrupt file falls back to the empty wardrobe.

## AI usage

AI tools were used to accelerate implementation against a spec written first in `planning.md`.

**1. Implementing the three tools (Milestone 3).**
- *Input given:* the Tool 1–3 spec blocks from `planning.md` (inputs, return shape, failure
  mode) plus the `load_listings()` signature and the listing field list.
- *Produced:* first-pass implementations of all three functions in `tools.py`.
- *What I changed/overrode:* tuned the relevance scoring (title/tags weighted 3× over
  description); switched size matching to case-insensitive substring to handle the messy
  dataset sizes; added the offline fallbacks for both LLM tools; set `create_fit_card`'s
  temperature to `1.0` after verifying lower values produced near-identical captions; added a
  stopword list so filler words don't pollute search scoring.

**2. Implementing the planning loop (Milestone 4).**
- *Input given:* the Planning Loop + State Management sections and the architecture diagram
  from `planning.md`.
- *Produced:* a first version of `run_agent` plus the query parser.
- *What I changed/overrode:* restructured it into an explicit `while` loop driven by
  `_decide_next_step` so the branching is centralized and auditable; added a `searched` flag to
  the session so the loop can tell "haven't searched yet" apart from "searched and found
  nothing" (both look like an empty list otherwise); kept the hybrid regex-first parser with an
  LLM fallback rather than parsing entirely with the LLM, to stay fast and deterministic for
  normal queries.

**3. Adding intent-based tool routing.**
- *Input given:* a description of the four intents I wanted (`full`, `outfit_only`,
  `price_check`, `caption_only`) and the rule that strong keyword signals should resolve
  directly with an LLM fallback for ambiguous cases.
- *Produced:* `_classify_intent` / `_llm_classify_intent` and the intent-aware
  `_decide_next_step`.
- *What I changed/overrode:* tightened the keyword rules so they only fire on *unambiguous*
  signals and defer conflicts to the LLM (e.g. a query with both "find" and "how to wear"
  goes to the LLM rather than guessing); extended `suggest_outfit` and `create_fit_card` to
  accept `new_item=None` so the no-search intents have a real item source (the wardrobe / the
  described look), per the design decision that outfit-only styles the existing wardrobe.

---

## Project structure

```
ai201-project2-fitfindr-starter/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe format + example/empty wardrobes
├── utils/
│   ├── data_loader.py         # load_listings(), get_example_wardrobe(), get_empty_wardrobe()
│   └── profile.py             # Style-profile persistence (stretch)
├── tools.py                   # The three tools + compare_price (stretch)
├── agent.py                   # Planning loop (run_agent) + session state
├── app.py                     # Gradio web interface
├── tests/test_tools.py        # Tool + failure-mode tests (pytest)
├── conftest.py                # Makes the project root importable for tests
├── planning.md                # Design spec (tools, loop, state, error handling, stretch)
└── requirements.txt
```
