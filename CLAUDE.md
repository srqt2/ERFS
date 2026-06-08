# ERFS — Equity Research Finance Skills

You are running in a workspace for producing pitch-deck-level research reports on individual public stocks.

## STOP — read this before doing anything else

If the user typed a ticker (or "research X" / "do X" / a company name), you MUST follow the contract below. **There is no other workflow.** Do not improvise. Do not paste a report inline. Do not call `anthropic-skills:pdf` to produce a freeform PDF. Do not invent valuation math.

Two analysts running the same ticker should produce identical Excel layouts and identical computed numbers given matching inputs. That only holds if every session follows this contract exactly.

## Output contract (mandatory)

Every research request produces **three files** in `output/<TICKER>/`:

1. **`<TICKER>.json`** — narrative report (thesis, catalysts, bear case, peers, business overview, etc). Matches `_schema/SPEC_v2.md`.
2. **`<TICKER>_valuation.json`** — pure math (assumptions with reasoning, scenarios with probabilities + reasoning, computed outputs, calculation_trace, sensitivity matrix). Matches `_schema/VALUATION_SCHEMA.md`. **Produced by running a Python compute script** — never by Claude inventing numbers.
3. **`<TICKER>.xlsx`** — Excel artifact rendered from the two JSONs by `_schema/render_excel.py`. **Locked 8-tab template** — never produced by `anthropic-skills:xlsx` or any other freeform Excel skill.

After producing the three files, Claude returns ONLY:
- The three file paths
- A one-paragraph headline (recommendation + target + top-line reason)

Do not include the JSON, the prose, the catalysts, or any other content inline in chat. The deliverables are the files.

## Why this contract exists — read carefully

This workspace is designed so two different Claude sessions (your desktop, your friend's web session, your phone) produce **identical output shape** on the same ticker. The narrative will read differently (different lenses, different angles — that's the feature). The math will be identical when inputs match (because Python is deterministic). The Excel layout will be identical (because `render_excel.py` uses a locked template).

If your output diverges in layout (different sheet count, different sheet names, different cell organization), you broke the contract. Re-read this file and try again.

## How to handle the user's first message

If they type a ticker, a company name, or "research X" / "do X" (examples: `NVDA`, `research Micron`, `Broadcom please`, `AVGO`), confirm the ticker once and run the cycle below.

If they ask what this is or say "hi" / "help" / "what do I do", reply briefly:

> Hi. This workspace produces stock research reports. Type a ticker symbol (like `NVDA` or `AAPL`) and I'll generate a full bull case, bear case, recommendation, and an Excel model. Takes 8-12 minutes.

If they ask anything off-topic, answer normally.

## The research cycle — skill-orchestrated (works in every environment)

This workspace uses **skills**, not sub-agent dispatch, for the bull/bear/audit passes. Skills work in Claude Code on desktop, web, and mobile. This eliminates the "Alpha doing all roles" problem when sub-agent dispatch is unavailable.

### Step 1 — Verify the price

Use `finance-market-analysis:yfinance-data` to fetch the live last close. Sanity-check it. If it looks off by an order of magnitude, retry with `Ticker.fast_info.last_price` and `Ticker.history(period='5d')`.

### Step 2 — Bull case lane

Invoke the `equity-bull-case` skill (via the Skill tool). Pass the ticker. The skill writes `output/<TICKER>/<TICKER>_bull.json` with the bull thesis, 8-10 catalysts, business facts, and a bull-side valuation lens.

### Step 3 — Bear case lane (independent)

Invoke the `equity-bear-case` skill. The skill is instructed to NOT read the bull file. It writes `output/<TICKER>/<TICKER>_bear.json` with the bear thesis sentence, 8-10 thesis-breakers, the mandatory 150-250 word bear paragraph, structural concerns, 5-7 key risks, what shorts are saying, and a bear-side valuation lens.

### Step 4 — Synthesize the narrative JSON

Read both `_bull.json` and `_bear.json`. Merge into `output/<TICKER>/<TICKER>.json` matching `_schema/SPEC_v2.md`:

- `thesis`: take three best bullets from the bull thesis_bullets (not all bull material — the synthesis writer's own three-bullet read)
- `bull_catalysts`: directly from `_bull.json`
- `bear_breakers`: directly from `_bear.json`
- `bear_paragraph`: directly from `_bear.json`
- `business_overview` etc: merge with preference for bull file's facts but check against bear file's structural concerns
- `peers`: pull from yfinance + bull `peers_positive_read` + bear `structural_concerns` for the rationale
- `key_risks`: directly from `_bear.json`

### Step 5 — Build the valuation inputs JSON

Choose the valuation method based on company type:

| Company type | Method |
|---|---|
| Industrials, software, semis with positive FCF | DCF |
| Banks, insurance with regular dividends | DDM |
| Multi-segment conglomerates (e.g. AVGO Semi+VMware, GEV Power+Wind) | SOTP |
| Loss-making growth | Multiple (EV/Sales) |

Write `output/<TICKER>/<TICKER>_inputs.json` matching `_schema/VALUATION_SCHEMA.md`. **Every numeric input has a `reasoning` field.**

The inputs JSON's `scenarios` array MUST include Bear / Base / Bull with these fields per scenario:

```jsonc
{
  "label": "Base",
  "key_changes": { "wacc": 0.095, "terminal_g": 0.035 },
  "probability": 0.55,
  "probability_reasoning": "Central case is most likely outcome over a 12m horizon. Hyperscaler capex plan visible through 2026, no near-term cyclical inflection.",
  "reasoning": "Base case narrative."
}
```

Probabilities across all scenarios PLUS the cross_check weight must sum to 1.0. For example:

- Bear 5% + Base 55% + Bull 20% + cross_check 20% = 100%

Write a `weights_reasoning` string at top level explaining the blended construction (why these proportions).

### Step 6 — Run the compute script

```bash
# Non-banks
python _schema/dcf_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json

# Banks
python _schema/ddm_compute.py --ticker <T>.JK --output output/<T>/<T>_valuation.json
# (or with overrides)
python _schema/ddm_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json

# Conglomerates
python _schema/sotp_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json
```

The script writes the valuation JSON with deterministic math. Same inputs always produce the same outputs.

### Step 7 — Optionally run the audit skill

If the valuation is numbers-heavy (DCF/DDM/SOTP, growth rates, multiples), invoke `equity-audit`. The skill reads the narrative + valuation JSONs, verifies the numbers, and writes `output/<TICKER>/<TICKER>_audit.json`.

For purely qualitative cycles (rare), the audit is optional.

### Step 8 — Voice-clean the prose

After writing the narrative JSON, run:

```bash
python _schema/voice_clean.py output/<TICKER>/<TICKER>.json
```

Zero em-dashes. No AI tells. Read `_schema/VOICE.md` if you're about to write any prose by hand.

### Step 9 — Render the Excel artifact

```bash
python _schema/render_excel.py --ticker <T> --category <AI or IDX or other>
```

This reads both JSONs and produces `output/<TICKER>/<TICKER>.xlsx` with 7 locked tabs: Cover, Assumptions, Calculation, Sensitivity, Scenarios (with probability weights + final target calc), Peers, Reasoning Log. Same JSON → same xlsx every time.

### Step 10 — Tell the user

Three files in `output/<TICKER>/`. Plus the headline.

Example:

> Done. Three files for NVDA in `output/NVDA/`:
>
> - `NVDA.xlsx` — Excel model (edit Assumptions tab to flex the target)
> - `NVDA_valuation.json` — pure math
> - `NVDA.json` — narrative
>
> **Recommendation: BUY, 12m target $X (+Y%).** Method: DCF with peer EV/EBITDA cross-check. Probability-weighted: Bear 10% × $A + Base 60% × $B + Bull 20% × $C + Cross-check 10% × $D. Top bull reason: CUDA + Blackwell roadmap. Top bear: hyperscaler insourcing + China H20 ban.

## Hard rules

- **ZERO `WebFetch` or `WebSearch`** as a default. Finance-skills only. If a skill returns thin data, add to `data_gaps` and proceed.
- **Bull and bear are independent.** The `equity-bear-case` skill is explicitly instructed not to read the bull file. The synthesizer in step 4 is the first time both views meet.
- **The agent does not touch Excel layout.** Excel is rendered by `_schema/render_excel.py`. Same JSON → same Excel across all sessions.
- **The agent does not invent valuation math.** Always run the compute script. Same inputs → same outputs.
- **Scenario probabilities must sum to 1.0** with the cross-check weight, and each must carry a `probability_reasoning` string.
- **Do not push or deploy.** This workspace produces files. The repo owner decides what to publish.
- **Be honest about gaps.** Set `data_gaps` to flag missing inputs.

## Why this contract exists

Two analysts (you, your friend) running the same ticker should produce:
- Different narratives — that's good, different lenses make the cross-check real
- Same Excel layout — locked by `render_excel.py`
- Same valuation outputs if inputs match — Python math is deterministic
- Same procedural independence — `equity-bear-case` cannot read the bull file regardless of which environment Claude is running in

The narrative breathes; the math is pinned; the bear case is independent. This is how reproducibility, creativity, and adversarial rigor coexist.

## API keys (optional)

- `FUNDA_API_KEY` — unlocks Funda AI REST (10-K segment splits, transcripts, supply chain)
- `ADANOS_API_KEY` — unlocks Adanos cross-source sentiment

Skills degrade gracefully without these. Add to `data_gaps` if absent.
