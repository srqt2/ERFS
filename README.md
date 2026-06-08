# ERFS — Equity Research Finance Skills workspace

Portable Claude Code workspace for single-ticker equity research. Produces **identical output shape** across every Claude environment (desktop, web, mobile, friend's laptop) because the math layer is locked Python scripts, not LLM-drafted prose.

## The output contract

Every research cycle produces **three files** per ticker in `output/<TICKER>/`:

| File | What it is | Produced by |
|---|---|---|
| `<TICKER>.json` | Narrative report — thesis, catalysts, bear case, peers, business overview, key risks | LLM (you / your friend) |
| `<TICKER>_valuation.json` | Pure math — assumptions with reasoning, scenarios with probabilities, full calculation trace, sensitivity matrix | Python compute scripts |
| `<TICKER>.xlsx` | 8-tab Excel workbook | `_schema/render_excel.py` (locked template) |

**Same input JSON → same Excel byte-for-byte.** That's the whole point. No agent ever touches Excel layout or invents valuation math.

The Excel has 8 tabs in a fixed order:

1. **Cover** — ticker, recommendation, target, headline thesis
2. **Assumptions** — every input cell with reasoning column (editable)
3. **Calculation** — DCF/DDM math results
4. **Formula trace** — step-by-step derivation of every number
5. **Sensitivity** — WACC × g_terminal (DCF) or Ke × g_terminal (DDM) grid
6. **Scenarios** — Bear / Base / Bull with probability weights + final blended target math
7. **Peers** — peer comparables table
8. **Reasoning log** — narrative explaining why this method, these assumptions, these peers

## Quick start (you / your friend)

Open a fresh Claude Code session against this repo. On the first user message, the SessionStart hook clones `himself65/finance-skills` into `.claude/skills/finance-skills/`. Then type a ticker:

```
NVDA
```

or

```
research Broadcom
```

or anything that reads as "I want a report on X". Claude follows `CLAUDE.md`'s 10-step pipeline:

1. Verifies price via `finance-market-analysis:yfinance-data`
2. Invokes `equity-bull-case` skill → writes `output/<T>/<T>_bull.json` (8-10 catalysts, business facts, bull valuation lens)
3. Invokes `equity-bear-case` skill — **does not read the bull file** → writes `output/<T>/<T>_bear.json` (8-10 thesis-breakers, bear paragraph, key risks)
4. Synthesizes both into `output/<T>/<T>.json` (the narrative)
5. Picks valuation method:
   - **DCF** for FCF-generating non-banks
   - **DDM** for banks
   - **SOTP** for multi-segment conglomerates
6. Writes `output/<T>/<T>_inputs.json` with every assumption and per-input reasoning, scenarios with probability + probability_reasoning, weights_reasoning at top
7. Runs the appropriate Python compute script (deterministic math):
   ```bash
   python _schema/dcf_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json
   # or
   python _schema/ddm_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json
   # or
   python _schema/sotp_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json
   ```
8. Optionally invokes `equity-audit` skill if the output is numbers-heavy
9. Voice-cleans the narrative:
   ```bash
   python _schema/voice_clean.py output/<T>/<T>.json
   ```
10. Renders the Excel artifact:
    ```bash
    python _schema/render_excel.py --ticker <T> --category AI
    ```

Three files land in `output/<TICKER>/`. The Excel has the 8-tab locked layout described above. The agent should report the file paths + the headline (recommendation, target, top-line thesis) — no inline report dump.

## Critical rules for your Claude session

**DO NOT** ask Claude to produce a PDF or DOCX directly via `anthropic-skills:pdf` / `anthropic-skills:docx` skills — those produce freeform output that diverges from the locked template. The Excel **is** the deliverable. If you need a PDF for sharing, future versions of this workspace will include `_schema/render_pdf.py` that renders from the same JSON.

**DO NOT** ask Claude to write the report inline in chat. The deliverables are the three files in `output/<TICKER>/`. Claude should only return file paths + a one-paragraph headline.

**DO NOT** ask Claude to "use a different valuation method" mid-flight. The method is chosen at step 5 and every compute script is deterministic for that method.

**DO** open Excel and edit the Assumptions tab when you want to flex the model. Numbers recalculate automatically (the formulas live in the workbook).

**DO** ask Claude to re-run a ticker if assumptions need revision — Claude rewrites `<T>_inputs.json`, re-runs the compute script, re-renders Excel. Narrative stays unless you specifically ask for a refresh.

## What makes outputs identical across sessions

| Layer | Variability | Why |
|---|---|---|
| `yfinance-data` (price, financials) | Very low (~99% same) | Same yfinance call returns same data given same date |
| Valuation method choice | Locked by company type | DCF for non-banks, DDM for banks |
| Valuation math | **Zero variance** | Python compute scripts; same inputs always produce same outputs |
| Bull/bear narrative (catalysts, breakers) | High by design | Different lenses make the cross-check real |
| Excel layout | **Zero variance** | `render_excel.py` uses a locked 8-tab template |

The **narrative breathes** (your friend's bull case will read differently from yours — that's the feature). The **math is pinned** (same Excel layout, same JSON shape, same computed numbers given matching inputs).

## Layout

```
.claude/
├── agents/
│   └── alpha.md            (legacy reference; the real orchestration is in CLAUDE.md)
├── skills/
│   ├── .gitkeep            (placeholder so the folder ships)
│   ├── banking-ddm/        bank valuation skill (DDM + Excess Returns + Justified P/B)
│   ├── equity-bull-case/   bull case generator skill
│   ├── equity-bear-case/   bear case generator skill (instructed NOT to read bull file)
│   └── equity-audit/       numerical audit skill
└── settings.json           SessionStart hook that clones himself65/finance-skills

_schema/
├── SPEC_v2.md              narrative JSON schema (industry_type, etc.)
├── VALUATION_SCHEMA.md     valuation JSON schema (scenarios, weights, calculation_trace)
├── VOICE.md                prose rules (zero em-dashes, no AI tells)
├── dcf_compute.py          DCF math engine (deterministic)
├── ddm_compute.py          DDM + Excess Returns + Justified P/B (deterministic)
├── sotp_compute.py         Sum of parts (multi-segment)
├── voice_clean.py          regex-based em-dash + AI-tell scrubber
└── render_excel.py         locked-template Excel renderer

output/<TICKER>/
├── <TICKER>.json           narrative
├── <TICKER>_inputs.json    LLM-supplied valuation assumptions
├── <TICKER>_valuation.json computed math (read this for the formula trace)
└── <TICKER>.xlsx           8-tab Excel

CLAUDE.md                   the contract Claude reads on session start
README.md                   this file
```

## API keys (optional)

Skills degrade gracefully if absent. Add to `data_gaps` in the narrative if missing.

| Variable | Unlocks |
|---|---|
| `FUNDA_API_KEY` | Funda AI REST endpoints (10-K segment splits, transcripts, supply chain, ownership flow) |
| `ADANOS_API_KEY` | Adanos cross-source sentiment (Reddit / X / news / Polymarket) |

## How to know your output matches mine

If yours and mine produce different reports on the same ticker:

1. **Diff the `<TICKER>_inputs.json` files.** The math is deterministic given inputs — different outputs imply different assumptions, and the difference is visible cell-by-cell in the inputs file.
2. **Diff the Excel files.** They should have identical 8-tab structure with the same row/column layout. Different values are fine (different assumptions); different layout means the renderer ran differently somehow.
3. **Check Claude followed `CLAUDE.md`.** If Claude went conversational instead of producing files, the session deviated from the contract. Re-prompt: "produce the three files per the CLAUDE.md contract."

If the layout is different, that's a bug — file an issue or tell the repo owner (Jan).

## Live published version

The repo owner publishes selected reports to https://intellidesk-nu.vercel.app/research. The Vercel `/valuation` pages show the same data shape that your Excel exports — narrative, assumptions with reasoning, scenarios with probability weights, formula trace, sensitivity matrix, downloadable Excel.

You can use those live pages as the canonical "what right looks like" reference if your Claude session output ever looks off.
