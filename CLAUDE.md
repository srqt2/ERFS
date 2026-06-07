# ERFS — Equity Research Finance Skills

You are running in a workspace for producing research reports on individual public stocks.

## How to handle the user's first message

**If they type a ticker, a company name, or "research X" / "do X"** (examples: `NVDA`, `research Micron`, `Broadcom please`, `AVGO`), confirm the ticker once in one line and then run the full research cycle. Do not ask clarifying questions.

**If they ask what this is or say "hi" / "help" / "what do I do",** reply briefly:

> Hi. This workspace produces stock research reports. Type a ticker symbol (like `NVDA` or `AAPL`) and I'll generate a full bull case, bear case, recommendation, and PDF. Takes about 8-12 minutes per report.

Then wait for them to type a ticker.

**If they ask anything off-topic,** answer normally.

## The research cycle (when a ticker is requested)

1. **Verify the price first.** Use `finance-market-analysis:yfinance-data` to fetch the live last close. If the value looks stale or wrong by an order of magnitude, cross-check with `Ticker.fast_info.last_price` and `Ticker.history(period='5d')` before continuing.

2. **Run two independent lanes in parallel.** In ONE message, dispatch `charlie` (bull case, `.claude/agents/charlie.md`) AND `kilo` (bear case, `.claude/agents/kilo.md`). Kilo never sees Charlie's draft. This is non-negotiable.

3. **Synthesize.** Blend both lanes into a recommendation, a 12-month target, and three probability-weighted paths.

4. **Audit if numbers-heavy.** If the output contains DCF, multiples, growth rates, or other quantitative anchors, run `delta` (`.claude/agents/delta.md`) for a numerical audit.

5. **Write the JSON.** Save to `output/<TICKER>/<TICKER>.json` matching `_schema/SPEC_v2.md` exactly. Required sections: header, recommendation, snapshot (20-25 entries), thesis (3 bullets), bear_paragraph, peers (5-7 rows), 8-10 bull_catalysts, 8-10 bear_breakers, dcf_scenarios (3), synthesis_paths (3 probs summing to 1.0), recommendation_table, catalysts_to_watch, data_gaps, business_overview, industry_position, historical_financials (4-5y P&L), management, key_risks (5-7), social_sentiment (optional).

6. **Voice-clean the prose.** Read `_schema/VOICE.md` and apply it. Zero em-dashes. No AI tells like "navigate", "leverage" (as verb), "comprehensive", "robust", "delve into", "Moreover", "Furthermore", "a testament to". After writing the JSON, run:

   ```bash
   python _schema/voice_clean.py output/<TICKER>/<TICKER>.json
   ```

   That script catches anything that leaked through.

7. **Render PDF and DOCX.** Use `anthropic-skills:pdf` to produce `output/<TICKER>/<TICKER>.pdf` (mobile-friendly A4) and `anthropic-skills:docx` to produce `output/<TICKER>/<TICKER>.docx` (editable Word). Both should render the JSON content as a clean report following the same section order as the schema.

## When you're done

Tell the user the headline and where the files are. Do not paste the full JSON. Example:

> Done. Three files for NVDA in `output/NVDA/`:
>
> - `NVDA.pdf` — read this on your phone
> - `NVDA.docx` — open in Word to edit
> - `NVDA.json` — raw data
>
> Tap any file in the left file tree to download.
>
> **Recommendation: BUY, 12m target $265 (+29% from $205).** The bull case is CUDA + Blackwell/Rubin dominance. The bear case is hyperscaler insourcing, the China H20 ban, and gross margins already at 75%.

## Hard rules

- **ZERO `WebFetch` or `WebSearch`.** Use only the `finance-*` skills installed by the SessionStart hook. If a skill returns thin data, add an entry to `data_gaps` and continue.
- **Charlie and Kilo must run in parallel.** If the Agent tool can't dispatch sub-agents in your environment, run them sequentially but pull INDEPENDENT data extracts so the lanes don't contaminate each other.
- **Do not push, deploy, or publish anything.** This workspace only produces files. The repo owner decides what to publish.
- **Be honest about gaps.** If Funda AI / Adanos / twitter-reader are unauthenticated, the report will be thinner on segment splits and sentiment. Say so in `data_gaps`. Don't fabricate.

## Optional API keys for deeper data

Set as environment variables. Skills degrade gracefully if absent:

- `FUNDA_API_KEY` — unlocks Funda AI REST (10-K segment splits, transcripts, supply chain, ownership flow)
- `ADANOS_API_KEY` — unlocks Adanos cross-source sentiment (Reddit / X / news / Polymarket)
