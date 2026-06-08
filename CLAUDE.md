# ERFS — Equity Research Finance Skills

You are running in a workspace for producing pitch-deck-level research reports on individual public stocks.

## Output contract (mandatory)

Every research request produces **THREE files** in `output/<TICKER>/`:

1. **`<TICKER>.json`** — narrative report (thesis, catalysts, bear case, peers, business overview, etc). Matches `_schema/SPEC_v2.md`.
2. **`<TICKER>_valuation.json`** — pure math (assumptions, computed outputs, sensitivities). Matches `_schema/VALUATION_SCHEMA.md`. Produced by running a compute script.
3. **`<TICKER>.xlsx`** — Excel artifact rendered from the two JSONs above by `_schema/render_excel.py`.

**Do not paste the report inline in chat.** Produce these three files. They are the deliverables.

## How to handle the user's first message

**If they type a ticker, a company name, or "research X" / "do X"** (examples: `NVDA`, `research Micron`, `Broadcom please`, `AVGO`), confirm the ticker once and run the full cycle.

**If they ask what this is** or say "hi" / "help" / "what do I do", reply briefly:

> Hi. This workspace produces stock research reports. Type a ticker symbol (like `NVDA` or `AAPL`) and I'll generate a full bull case, bear case, recommendation, and an Excel model with the valuation math. Takes 8-12 minutes per report.

## The research cycle (when a ticker is requested)

### Step 1 — Verify the price

Use `finance-market-analysis:yfinance-data` to fetch the live last close. Sanity check it. If it looks off by an order of magnitude, retry with `Ticker.fast_info.last_price` and `Ticker.history(period='5d')`.

### Step 2 — Run two independent narrative lanes in parallel

In ONE message, dispatch `charlie` (bull, `.claude/agents/charlie.md`) AND `kilo` (bear, `.claude/agents/kilo.md`). Kilo never sees Charlie's draft. This is non-negotiable.

### Step 3 — Synthesize the narrative JSON

Produce `output/<TICKER>/<TICKER>.json` matching `_schema/SPEC_v2.md`. Required sections: header, recommendation, snapshot (20-25 entries), thesis (3 bullets), bear_paragraph, peers (5-7 rows), 8-10 bull_catalysts, 8-10 bear_breakers, recommendation_table, catalysts_to_watch, data_gaps, business_overview, industry_position, historical_financials (4-5y P&L), management, key_risks (5-7).

### Step 4 — Choose the valuation method

| Company type | Primary method |
|---|---|
| Industrials, software, semis with positive FCF | DCF |
| Banks, insurance with regular dividends | DDM |
| Multi-segment conglomerates (e.g. AVGO Semi+VMware) | SOTP |
| Loss-making growth companies | Multiple (EV/Sales) |
| REITs, asset-heavy miners | NAV (not yet supported; use DCF for v1) |

Write a brief reasoning for the choice.

### Step 5 — Write the inputs JSON

For DCF: write `output/<TICKER>/<TICKER>_inputs.json` matching `_schema/VALUATION_SCHEMA.md`. **Every numeric input gets a `reasoning` field.** Pick WACC components, terminal growth, FCF projections for the forecast horizon (typically 5-7 years), and bull/base/bear scenarios.

For DDM: you can skip the inputs file and let `ddm_compute.py` auto-populate from yfinance, OR write an overrides JSON if you have country / sector reasoning that differs from defaults.

For SOTP: write a segments array; each segment specifies its method (DCF or Multiple) and its inputs.

### Step 6 — Run the compute script

```bash
# DCF
python _schema/dcf_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json

# DDM (banks)
python _schema/ddm_compute.py --ticker <T>.JK --output output/<T>/<T>_valuation.json

# DDM with overrides
python _schema/ddm_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json

# SOTP
python _schema/sotp_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json
```

The script reads inputs, runs deterministic Python math, writes the valuation JSON.

### Step 7 — Render the Excel artifact

```bash
python _schema/render_excel.py --ticker <T> --category <AI or IDX>
```

This reads both JSONs and produces `output/<T>/<T>.xlsx` with 7 locked tabs: Cover, Assumptions, Calculation, Sensitivity, Scenarios, Peers, Reasoning Log. Same JSON → same xlsx every time.

### Step 8 — Voice-clean the prose

Read `_schema/VOICE.md`. Zero em-dashes. No AI tells. After writing the narrative JSON, run:

```bash
python _schema/voice_clean.py output/<TICKER>/<TICKER>.json
```

That script catches anything that leaked through.

### Step 9 — Tell the user

Three files in `output/<TICKER>/`:
- `<TICKER>.xlsx` — open in Excel, edit Assumptions tab to see new target
- `<TICKER>_valuation.json` — full math
- `<TICKER>.json` — narrative

Plus headline: recommendation, target, blended math, top reason.

Example:

> Done. Three files for NVDA in `output/NVDA/`:
>
> - `NVDA.xlsx` — Excel model (edit Assumptions tab to flex the target)
> - `NVDA_valuation.json` — pure math
> - `NVDA.json` — narrative
>
> **Recommendation: BUY, 12m target $X (+Y% from spot).** Method: DCF with peer EV/EBITDA cross-check. Bull case is CUDA + Blackwell. Bear case is hyperscaler insourcing + China H20 ban + 75% gross margins already.

## Hard rules

- **ZERO `WebFetch` or `WebSearch`.** Finance-skills only.
- **Charlie and Kilo must run in parallel.** If the Agent tool can't dispatch sub-agents in your environment, run them sequentially but pull independent data extracts.
- **The agent does not touch Excel layout.** Excel is rendered by `_schema/render_excel.py` from the JSON. Same JSON → same Excel byte-for-byte across all sessions.
- **The agent does not invent valuation math.** Always run the compute script. Same inputs → same outputs.
- **Do not push or deploy anything.** This workspace only produces files. The repo owner decides what to publish.
- **Be honest about gaps.** Set `data_gaps` to flag missing inputs (Funda AI / Adanos / Twitter unauthenticated for IDX, etc).

## Why this contract exists

Two analysts (you, your friend) running the same ticker should produce:
- **Different narratives** — that's good, different lenses make the cross-check real
- **Same Excel layout** — locked by `render_excel.py`
- **Same valuation outputs if inputs match** — Python math is deterministic

The narrative breathes; the math is pinned. This is the only way reproducibility and creativity coexist.

## API keys (optional)

- `FUNDA_API_KEY` — unlocks Funda AI REST (10-K segment splits, transcripts, supply chain)
- `ADANOS_API_KEY` — unlocks Adanos cross-source sentiment

Skills degrade gracefully without these. Add to `data_gaps` if absent.
