# Example output shape

Concrete reference for what your three output files should look like. Use as a template when in doubt.

If your output doesn't match this shape, you broke the contract in `CLAUDE.md`. Re-read it and try again.

---

## File 1: `output/<TICKER>/<TICKER>.json` — narrative

Required top-level fields (see `_schema/SPEC_v2.md` for full schema):

```jsonc
{
  "ticker": "AVGO",
  "name": "Broadcom Inc.",
  "listings": "NASDAQ",
  "sector": "Technology — Semiconductors & Infrastructure Software",
  "date": "2026-06-08",
  "engine": "himself65/finance-skills (yfinance-data, company-valuation, stock-correlation, ...)",
  "industry_type": "non-bank",   // or "bank" for banks
  "recommendation": {
    "action": "BUY with positive bias",
    "tone": "positive",            // "positive" | "neutral" | "negative"
    "current_price": 385.73,
    "target_12m": 477,
    "upside_pct": 23.7,
    "rationale": "Probability-weighted: 10% × $191 + 55% × $416 + 15% × $712 + 20% × $444 = $443",
    "next_earnings": "2026-09-04 (Q3 FY26)"
  },
  "snapshot": [
    {"label": "Last close", "value": "$385.73"},
    {"label": "Market cap", "value": "$1.83T"},
    // 20-25 entries
  ],
  "thesis": [
    "Bullet 1 — the most important reason.",
    "Bullet 2 — the supporting structural driver.",
    "Bullet 3 — the math: why current price is below intrinsic."
  ],
  "bear_paragraph": "150-250 word coherent narrative where this stock falls 30-50% in 12-24 months.",
  "peers": [
    {"ticker": "AVGO", "pe_ntm": 25.4, "ev_ebitda": 28.0, "rev_growth_ttm": 18.5, "ytd": 12.0, "y1": 35.2, "highlight": true},
    // 5-7 peer rows
  ],
  "peers_read": "1-paragraph interpretation of the peer comp.",
  "bull_catalysts": [
    {"id": 1, "title": "Title under 80 chars", "body": "80-200 words. Quantified. Dated."},
    // 8-10 catalysts
  ],
  "bear_breakers": [
    {"id": 1, "title": "...", "body": "..."},
    // 8-10 thesis-breakers
  ],
  "dcf_scenarios": [],   // empty array — the real scenarios live in the valuation JSON
  "synthesis_paths": [
    {"label": "Bull", "prob": 0.20, "outcome_range": "$500–600", "description": "1-2 sentences."},
    {"label": "Base", "prob": 0.55, "outcome_range": "$420–470", "description": "..."},
    {"label": "Bear", "prob": 0.25, "outcome_range": "$300–380", "description": "..."}
  ],
  "recommendation_table": [
    {"action": "Direction", "detail": "BUY with positive bias"},
    {"action": "12m target", "detail": "$477"},
    {"action": "Existing longs", "detail": "Hold; trim to 2.0% gross if rallying above $510"},
    {"action": "New money", "detail": "Initiate in 0.5% tranches; full position at $360 base"},
    {"action": "Hedge", "detail": "Aug $440/$380 put spread on 50% of position"},
    {"action": "Position sizing", "detail": "2.0–2.5% gross"}
  ],
  "catalysts_to_watch": [
    "Q3 FY26 earnings — September 4, 2026 (cons $29.44B rev / $3.24 EPS)",
    "Hyperscaler XPU customer count update on Q3 call",
    // 5-7 items
  ],
  "data_gaps": [
    "Funda AI MCP unauthenticated — segment splits estimated from yfinance long-form business summary",
    "Adanos sentiment API key not configured — social_sentiment omitted"
  ],
  "business_overview": {
    "summary": "1-paragraph description.",
    "business_model": "1-paragraph revenue model.",
    "segments": [
      {"name": "Semiconductor Solutions", "revenue_share_pct": 56, "growth_yoy_pct": 22, "margin_pct": 40, "description": "..."},
      // 3+ segments
    ],
    "customers": [
      {"name": "Google", "revenue_share_pct": null, "importance": "Custom silicon hyperscaler partner since 2017"}
    ],
    "geographic_revenue": [
      {"region": "China", "pct": 32}
    ]
  },
  "industry_position": {
    "market_overview": "1-paragraph industry framing.",
    "tam_usd_bn": 250,
    "moat": "1-paragraph competitive moat.",
    "competitors": [
      {"name": "NVDA", "description": "AI accelerator pure-play; doesn't compete in custom silicon directly."}
    ]
  },
  "historical_financials": [
    {"fy": "FY22", "revenue_usd_b": 33.2, "revenue_growth_yoy_pct": 21, "gross_margin_pct": 75, "ebitda_usd_m": 20800, "ebitda_margin_pct": 62.7, "net_income_usd_m": 11300, "fcf_usd_m": 16300, "eps": 26.86},
    // 4-5 years
  ],
  "management": {
    "ceo": "Hock Tan, CEO since 2006",
    "cfo": "Kirsten Spears, CFO since 2020",
    "insider_ownership_pct": 1.4,
    "capital_allocation_history": "1-paragraph buybacks / dividends / M&A summary",
    "recent_insider_activity": [
      {"date": "2026-04-15", "insider": "Hock Tan, CEO", "action": "Sale", "value_usd_m": 12.5}
    ]
  },
  "key_risks": [
    {"category": "customer", "title": "Customer concentration", "description": "Top 3 customers = 35% of revenue per 10-K disclosure..."},
    // 5-7 entries
  ]
}
```

---

## File 2: `output/<TICKER>/<TICKER>_valuation.json` — math

Produced by `python _schema/dcf_compute.py --inputs <T>_inputs.json --output <T>_valuation.json` (or `ddm_compute.py` for banks). **Never hand-written.**

```jsonc
{
  "schema_version": "1.0",
  "ticker": "AVGO",
  "currency": "USD",
  "current_price": 385.73,
  "shares_outstanding_b": 4.73,
  "net_debt_b": 58.0,
  "computed_at": "2026-06-08T12:00:00Z",
  "primary_method": {
    "name": "DCF",
    "category": "intrinsic",
    "reasoning": "Why DCF for this company.",
    "inputs": {
      "wacc": {
        "value": 0.095,
        "components": {"rf": 0.044, "beta": 1.05, "erp": 0.055, "debt_weight": 0.15, "cost_of_debt_after_tax": 0.035},
        "components_reasoned": {
          "rf": {"value": 0.044, "reasoning": "US 10Y Treasury yield."},
          "beta": {"value": 1.05, "reasoning": "yfinance 5Y beta vs SPX."},
          "erp": {"value": 0.055, "reasoning": "DM equity risk premium, Damodaran 2025."}
        },
        "reasoning": "Overall WACC justification."
      },
      "terminal_growth": {"value": 0.035, "reasoning": "Above-GDP for AI accelerator + software."},
      "forecast_horizon_years": 7,
      "fcf_projections": [
        {"year": 2026, "fcf_b": 32.5, "revenue_b": 60.0, "revenue_growth_pct": 9.0, "ebitda_margin": 0.62, "rationale": "..."},
        // ... 6 more years
      ]
    },
    "outputs": {
      "pv_explicit_fcf_b": 158.3,
      "terminal_value_b": 1247.5,
      "pv_terminal_b": 661.4,
      "implied_ev_b": 819.7,
      "implied_equity_b": 761.7,
      "implied_px": 161.05,
      "calculation_trace": {
        "wacc": {
          "formula": "WACC = (1 - D/V) x Ke + (D/V) x Kd_after_tax",
          "steps": [
            {"label": "Cost of equity (Ke)", "expression": "0.044 + 1.05 x 0.055", "result": "0.1018"},
            {"label": "Equity weight x Ke", "expression": "0.85 x 0.1018", "result": "0.0866"},
            {"label": "Debt weight x after-tax Kd", "expression": "0.15 x 0.035", "result": "0.00525"},
            {"label": "WACC", "expression": "0.0866 + 0.00525", "result": "0.0919 (9.19%)"}
          ]
        },
        // explicit_fcf, terminal_value, bridge_to_implied_px
      }
    },
    "sensitivity": {
      "rows_label": "WACC",
      "cols_label": "Terminal g",
      "row_values": [0.085, 0.090, 0.095, 0.100, 0.105],
      "col_values": [0.025, 0.030, 0.035, 0.040, 0.045],
      "implied_px_matrix": [[...], [...], ...]
    }
  },
  "cross_check": {
    "name": "Peer EV/EBITDA",
    "category": "relative",
    "reasoning": "Why this multiple, why these peers.",
    "inputs": {"multiple_type": "EV/EBITDA", "peer_median_multiple": 15.0, "fy_estimate": 35.0, "peers_used": ["NVDA", "MRVL", "AMD"]},
    "outputs": {"implied_ev_b": 525.0, "implied_equity_b": 467.0, "implied_px": 98.73}
  },
  "scenarios": [
    {
      "label": "Bear",
      "key_changes": {"wacc": 0.105, "terminal_g": 0.025, "fcf_multiplier": 0.85},
      "implied_px": 191.0,
      "probability": 0.10,
      "probability_reasoning": "10% probability. Requires hyperscaler capex digestion year + WACC spike. Either alone is more likely; compound is 10%.",
      "reasoning": "Hyperscaler capex digestion year. FCF -15% vs base."
    },
    {"label": "Base", "key_changes": {}, "implied_px": 415.61, "probability": 0.55, "probability_reasoning": "...", "reasoning": "..."},
    {"label": "Bull", "key_changes": {"wacc": 0.09, "terminal_g": 0.04, "fcf_multiplier": 1.15}, "implied_px": 712.48, "probability": 0.15, "probability_reasoning": "...", "reasoning": "..."}
  ],
  "blended_target": 443.4,
  "blending_logic": "55% Base + 10% Bear + 15% Bull + 20% Peer EV/EBITDA",
  "blending_weights": {"cross_check": 0.20},
  "weights_reasoning": "Slight bull skew given AI capex visibility through 2026 but capped by customer concentration risk. Cross-check at 20% anchors DCF assumptions against market reality.",
  "weighted_contributions": [
    {"component": "Bear", "implied_px": 191.0, "weight": 0.10, "contribution": 19.10, "reasoning": "..."},
    {"component": "Base", "implied_px": 415.61, "weight": 0.55, "contribution": 228.59, "reasoning": "..."},
    {"component": "Bull", "implied_px": 712.48, "weight": 0.15, "contribution": 106.87, "reasoning": "..."},
    {"component": "Peer EV/EBITDA", "implied_px": 98.73, "weight": 0.20, "contribution": 19.75, "reasoning": "..."}
  ],
  "weight_total_check": 1.0,
  "upside_pct": 15.0
}
```

---

## File 3: `output/<TICKER>/<TICKER>.xlsx` — Excel

Produced by `python _schema/render_excel.py --ticker <T> --category AI`. **Never written by hand or by `anthropic-skills:xlsx`.**

The locked 8-tab template:

| # | Tab | Content |
|---|---|---|
| 1 | Cover | Ticker, recommendation, current price, target, upside %, valuation method, blended target, three thesis bullets |
| 2 | Assumptions | Every input cell with reasoning in column C. Editable — change WACC, Excel recalculates target |
| 3 | Calculation | DCF/DDM math output rows |
| 4 | Formula trace | Step-by-step derivation of every derived number |
| 5 | Sensitivity | WACC × g_term (DCF) or Ke × g_term (DDM) matrix |
| 6 | Scenarios | Bear / Base / Bull with probability + weighted contribution + final blended target math |
| 7 | Peers | Peer comparables table with the subject ticker highlighted |
| 8 | Reasoning log | Narrative explaining the method, key assumptions, peer choice |

If your Excel has a different number of tabs, different tab names, different column ordering, or different cell structure, your output broke the contract. Re-render via `render_excel.py`.

---

## What your output should NOT look like

Anti-patterns the friend's session has been hitting:

- ❌ **PDF generated by `anthropic-skills:pdf`** with freeform layout — diverges from our template every run
- ❌ **Excel generated by `anthropic-skills:xlsx`** without the 8-tab structure — same issue
- ❌ **Narrative pasted inline in chat** — the deliverable is the file, not the chat message
- ❌ **Valuation math computed by Claude in a Python REPL inline** — the deliverable is the JSON produced by `dcf_compute.py` / `ddm_compute.py`, not a one-off calculation
- ❌ **Different scenario labels** ("Optimistic / Realistic / Pessimistic" instead of "Bear / Base / Bull") — breaks Excel rendering
- ❌ **Missing `probability` or `probability_reasoning` on scenarios** — Excel will warn "weights don't sum to 1.0"
- ❌ **Missing per-input reasoning** (`rf_reasoning`, `erp_reasoning`, etc.) — auto-populated reasoning from the compute script is the fallback, but each input should have one

## Compare your output to canonical published versions

The repo owner publishes live versions of the same ticker shape at https://intellidesk-nu.vercel.app/research. Pick any ticker, look at:

- Its `/research/<cat>/<TICKER>/valuation` page — the structure of assumptions, scenarios, formula trace
- Its `/research/<cat>/<TICKER>.xlsx` download — the 8-tab template

If your output looks structurally different, your session deviated.
