# Valuation JSON schema

Companion to the narrative JSON. Records the math behind the price target so it's auditable, reproducible, and renderable to Excel.

**Path:** `output/<TICKER>/<TICKER>_valuation.json`

## The principle

The LLM picks the assumptions based on its read of the company. Python does the math. Same inputs always produce the same outputs. The reasoning for each assumption sits next to the assumption itself, so any reader can see why a given WACC or terminal growth was chosen.

## Top-level shape

```ts
{
  schema_version: "1.0",
  ticker: string,                    // "AVGO"
  currency: "USD" | "IDR" | "EUR" | ...,
  current_price: number,
  shares_outstanding_b: number,      // in billions
  net_debt_b: number,                // billions; negative if net cash
  computed_at: string,               // ISO 8601

  primary_method: ValuationMethod,
  cross_check?: ValuationMethod,
  scenarios: Scenario[],             // exactly 3: Bear, Base, Bull

  blended_target: number,
  blending_logic: string,            // e.g. "60% Base DCF + 20% Bull DCF + 20% Peer multiple"
  upside_pct: number                 // (blended - current) / current * 100
}
```

## ValuationMethod

Any valuation method (DCF, DDM, SOTP, Multiple) shares this outer shape:

```ts
{
  name: "DCF" | "DDM" | "SOTP" | "Multiple",
  category: "intrinsic" | "relative" | "asset-based",
  reasoning: string,                 // 1-2 sentences: why this method for this company
  inputs: { ... method-specific ... },
  outputs: {
    implied_px: number,
    implied_ev_b?: number,
    implied_equity_b?: number,
    ... method-specific ...
  },
  sensitivity?: {
    rows_label: string,              // "WACC" or "Ke"
    cols_label: string,              // "Terminal g"
    row_values: number[],            // decimal, e.g. [0.085, 0.090, 0.095, 0.100, 0.105]
    col_values: number[],
    implied_px_matrix: (number | null)[][]
  }
}
```

## Scenario

```ts
{
  label: "Bear" | "Base" | "Bull",
  key_changes: object,               // assumption deltas; e.g. {"wacc": 0.105, "terminal_g": 0.025}
  implied_px: number,
  probability: number,               // 0..1, see weighting rule below
  probability_reasoning: string,     // why this probability for this scenario
  reasoning: string                  // 1 sentence: what world this assumes
}
```

## Weighting rule

The scenario probabilities PLUS the cross-check weight must sum to 1.0.

Example:

```jsonc
{
  "scenarios": [
    {"label": "Bear", "probability": 0.05, ...},
    {"label": "Base", "probability": 0.55, ...},
    {"label": "Bull", "probability": 0.20, ...}
  ],
  "blending_weights": {
    "cross_check": 0.20
  }
}
```

Total = 0.05 + 0.55 + 0.20 + 0.20 = 1.00. The blended target is:

```
target = Σ(scenario.probability × scenario.implied_px) + cross_check_weight × cross_check.outputs.implied_px
```

A top-level `weights_reasoning` field explains why the LLM chose these particular weights.

## DCF inputs

```ts
{
  wacc: {
    value: number,                   // decimal, e.g. 0.095
    components: {
      rf: number,                    // risk-free rate
      beta: number,
      erp: number,                   // equity risk premium
      debt_weight: number,           // weight of debt in capital structure
      cost_of_debt_after_tax: number
    },
    reasoning: string
  },
  terminal_growth: { value: number, reasoning: string },
  forecast_horizon_years: number,    // typically 5-10
  fcf_projections: Array<{
    year: number,                    // 2026, 2027, ...
    fcf_b: number,                   // billions, denominated in `currency`
    revenue_b?: number,              // optional but populated by agent
    revenue_growth_pct?: number,
    ebitda_margin?: number,
    rationale: string                // why this FCF in this year
  }>
}
```

## DCF outputs (computed by `dcf_compute.py`)

```ts
{
  pv_explicit_fcf_b: number,
  terminal_value_b: number,
  pv_terminal_b: number,
  implied_ev_b: number,
  implied_equity_b: number,
  implied_px: number
}
```

## DDM inputs (banks)

```ts
{
  rf: number,
  erp: number,
  beta: number,
  beta_reasoning: string,
  d0: number,                        // sustainable per-share dividend
  d0_method: string,                 // how it was normalized
  book_value_per_share: number,
  roe: number,                       // decimal
  g_high: number,                    // years 1-5 growth rate
  g_terminal: number,
  g_book: number,                    // book value growth used in excess returns
  years_high: number,
  years_decline: number,
  horizon_excess_returns: number,
  max_payout_cap: number,            // default 0.70
  reasoning: string
}
```

## DDM outputs

```ts
{
  cost_of_equity_pct: number,
  ddm: { implied_value: number, pv_explicit: number, pv_terminal: number, explicit_dividends: number[] },
  excess_returns: { implied_value: number, book_value: number, pv_excess: number },
  justified_pb: { ratio: number, implied_value: number },
  blended_implied_value: number,
  implied_px: number                 // alias for blended_implied_value
}
```

## Multiple (peer cross-check) inputs

```ts
{
  multiple_type: "EV/EBITDA" | "P/E" | "P/B" | "EV/Sales",
  peer_median_multiple: number,
  fy_estimate: number,               // FY26E EBITDA in billions, or EPS, etc.
  peers_used: string[],              // tickers
  reasoning: string                  // why these peers, why this multiple
}
```

## How agents use this

1. Pick the primary method based on the company type:
   - Industrials, software, semis with positive FCF: `DCF`
   - Banks, REITs: `DDM`
   - Multi-segment conglomerates: `SOTP` (combination of methods, one per segment)
   - Loss-making growth companies: `Multiple` (EV/Sales)
2. Write a brief reasoning for the method choice.
3. Populate the inputs block. Every numeric input gets a `reasoning` string next to it.
4. Write the inputs to `output/<TICKER>/<TICKER>_inputs.json`.
5. Run the compute script:
   - DCF: `python _schema/dcf_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json`
   - DDM: `python _schema/ddm_compute.py --ticker <T>.JK --output output/<T>/<T>_valuation.json --inputs <optional overrides>`
6. The script writes the valuation JSON in the canonical shape above.
7. Read the blended target from the valuation JSON and put it in the narrative JSON's `recommendation` block.

## How renderers use this

- `render_excel.py` reads both JSONs and produces a 7-tab xlsx: Cover, Assumptions, Calculation, Sensitivity, Scenarios, Peers, Reasoning Log.
- `render_pdf.py` (planned) reads the narrative JSON for the prose and the valuation JSON for the math, produces a PDF that matches the web page layout.
- Next.js renders the narrative JSON at `/research/[cat]/[ticker]` and the valuation JSON at `/research/[cat]/[ticker]/valuation`.
