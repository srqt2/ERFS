# Research JSON Schema v2 — pitch-deck-level

Single-ticker research notes published to IntelliDesk `/research/<TICKER>`.

## Engine

`himself65/finance-skills` only. **ZERO WebFetch / WebSearch under any circumstances.** If a skill returns thin data, document the gap in `data_gaps`. Do NOT patch with WebFetch.

## Skills to use (mandatory — pull from each, in this order)

1. **`finance-market-analysis:yfinance-data`** — price, 5-yr OHLC, financials (income/balance/cash flow), options chains, analyst targets, institutional holders, insider transactions
2. **`finance-data-providers:funda-data`** — MCP first (`agent_chat`) for analyst-grade research synthesis: segment-level revenue split, top customers, supply-chain mapping, transcript reads, 10-K extracts, ownership flow. REST API as fallback for raw data.
3. **`finance-market-analysis:company-valuation`** — DCF + relative multiples + SOTP triangulation with sensitivity matrix
4. **`finance-market-analysis:earnings-recap`** — last 4 quarters actual vs estimate, margin trend
5. **`finance-market-analysis:estimate-analysis`** — analyst revision trends, distribution
6. **`finance-market-analysis:stock-correlation`** — peer set + co-movement
7. **`finance-market-analysis:stock-liquidity`** — spreads, ADTV, market impact
8. **`finance-data-providers:finance-sentiment`** — Adanos cross-source sentiment (Reddit/X/news/Polymarket)
9. **`finance-social-readers:twitter-reader`** — X chatter on ticker
10. **`finance-social-readers:opencli-reader`** — Yahoo Finance / Reddit r/investing fallback (NOT WebFetch)
11. **`finance-data-providers:tradingview-reader`** — only if user's TV is open (silently skip otherwise)

## Mandatory bull/bear contract

Run TWO independent passes:
1. **BULL lane** — long thesis, 8-10 catalysts. Independent data extracts. Don't peek at bear.
2. **BEAR lane** — short thesis, 8-10 thesis-breakers. Independent data extracts. Don't peek at bull.
Then **synthesize** — three probability-weighted paths, blended target.

## Output

Write a SINGLE JSON file to:
```
output/<TICKER>/<TICKER>.json
```

UPPERCASE ticker, no exchange suffix.

## Schema (TypeScript)

```ts
interface ReportPayload {
  // ===== HEADER =====
  ticker: string;                    // "AVGO"
  name: string;                      // "Broadcom Inc."
  listings: string;                  // "NASDAQ" or "NYSE / TSX"
  sector: string;                    // "Technology — Semiconductors"
  date: string;                      // "2026-06-07"
  engine: string;                    // skills used, comma-separated

  // ===== RECOMMENDATION =====
  recommendation: {
    action: string;                  // "BUY", "BUY with positive bias", "HOLD", "HOLD with negative bias", "SELL", etc.
    tone: "positive" | "negative" | "neutral";
    current_price: number;           // 0.00
    target_12m: number;              // 0
    upside_pct: number;              // +/-
    rationale: string;               // "30% × $X + 40% × $Y + 30% × $Z = $T"
    next_earnings: string;           // "2026-07-28 (Q2 FY26 · cons $4.38B rev / $2.31 EPS)"
  };

  // ===== KEY NUMBERS — 20-25 entries =====
  snapshot: Array<{ label: string; value: string }>;
  // Required entries (use these labels):
  //   Last close, 52-week range, Drawdown from high, 1-year return, YTD return,
  //   Market cap, Enterprise value, Net debt, FY25 revenue, FY25 EBITDA / margin,
  //   FY25 FCF, Forward EPS (FY26E), Forward P/E, EV / EBITDA (TTM), Beta,
  //   ADTV (30d, $), 30d realized vol, Analyst consensus, Mean / median PT,
  //   Implied PT upside (Street), Dividend yield (if applicable)

  // ===== THESIS =====
  thesis: string[];                  // EXACTLY 3 bullets

  // ===== BEAR PARAGRAPH (mandatory contract) =====
  bear_paragraph: string;            // 150-250 words, written independently

  // ===== PEERS =====
  peers: Array<{
    ticker: string;
    pe_ntm: number;
    ev_ebitda: number;
    rev_growth_ttm: number;          // percent
    ytd: number;                     // percent
    y1: number;                      // percent
    highlight?: boolean;             // true for the subject ticker
  }>;
  peers_read: string;                // 1-paragraph interpretation

  // ===== BULL CATALYSTS (HTML card map) — 8-10 entries =====
  bull_catalysts: Array<{
    id: number;                      // 1..N
    title: string;                   // short, < 80 chars
    body: string;                    // 80-200 words, quantified
  }>;

  // ===== BEAR THESIS-BREAKERS (HTML card map) — 8-10 entries =====
  bear_breakers: Array<{
    id: number;
    title: string;
    body: string;
  }>;

  // ===== INDUSTRY TYPE (optional, default "non-bank") =====
  // When "bank", populate `bank_valuation` below and leave `dcf_scenarios` empty.
  // For everything else, use `dcf_scenarios` and leave `bank_valuation` unset.
  industry_type?: "bank" | "non-bank";

  // ===== DCF SCENARIOS (non-bank only) =====
  dcf_scenarios: Array<{
    scenario: "Bear" | "Base" | "Bull";
    wacc: string;                    // "10.0%"
    terminal_g: string;              // "3.0%"
    fcf_path: string;                // "+35/+28/+20/+15/+10%"
    implied_px: number;
  }>;

  // ===== BANK VALUATION (banks only) =====
  // Populate this block for banks via the `banking-ddm` skill. Leave
  // `dcf_scenarios` empty when `industry_type` is "bank".
  bank_valuation?: {
    cost_of_equity_pct: number;          // Ke via CAPM
    risk_free_rate_pct: number;          // local 10Y govt bond
    equity_risk_premium_pct: number;
    beta: number;
    beta_warning?: string;               // present if yfinance beta was sanitized
    current_dividend_per_share: number;  // normalized D0 (special divs filtered)
    d0_method: string;                   // how D0 was estimated
    book_value_per_share: number;
    roe_pct: number;
    current_pb: number;
    ddm_high_growth_pct: number;         // years 1-5
    ddm_terminal_growth_pct: number;
    ddm_implied_px: number;
    excess_returns_implied_px: number;
    justified_pb_ratio: number;          // (ROE - g) / (Ke - g)
    justified_pb_implied_px: number;     // ratio x BV per share
    blended_implied_px: number;          // 40/40/20 weighted across the three
    upside_pct: number;                  // blended vs current
    sensitivity: {
      ke_range_pct: number[];            // 5 points around central Ke
      g_range_pct: number[];             // 5 points around central g_terminal
      implied_px_matrix: (number | null)[][];
    };
  };

  // ===== SYNTHESIS — 3 paths =====
  synthesis_paths: Array<{
    label: "Bull" | "Base" | "Bear";
    prob: number;                    // 0..1, must sum to 1.0
    outcome_range: string;           // "$405–450"
    description: string;             // 1-2 sentences
  }>;

  // ===== RECOMMENDATION TABLE =====
  recommendation_table: Array<{ action: string; detail: string }>;
  // Required actions:
  //   Direction, 12m target, Existing longs, New money, Hedge, Position sizing

  // ===== CATALYSTS =====
  catalysts_to_watch: string[];      // 5-7 entries

  // ===== DATA GAPS =====
  data_gaps: string[];               // anything skill stack could not pull

  // ===== EXTENDED — pitch-deck level (REQUIRED for v2) =====

  business_overview: {
    summary: string;                 // 1-paragraph what the company does (50-100 words)
    business_model: string;          // 1-paragraph revenue model (50-100 words)
    segments: Array<{
      name: string;                  // "CCS (Connectivity & Cloud Solutions)"
      revenue_share_pct?: number;    // 0-100, use null if not disclosed
      growth_yoy_pct?: number;
      margin_pct?: number;
      description: string;           // 1-2 sentences
    }>;
    geographic_revenue?: Array<{ region: string; pct: number }>;  // optional if not disclosed
    customers?: Array<{
      name: string;                  // "Meta Platforms"
      revenue_share_pct?: number;    // null if disclosed only as "top 10"
      importance: string;            // 1 line
    }>;
  };

  industry_position: {
    market_overview: string;         // 1-paragraph industry context
    tam_usd_bn?: number;             // total addressable market
    moat: string;                    // 1-paragraph competitive moat
    competitors: Array<{ name: string; description: string }>;  // 3-5 entries
  };

  historical_financials: Array<{
    fy: string;                      // "FY22"
    revenue_usd_b: number;
    revenue_growth_yoy_pct?: number;
    gross_margin_pct?: number;
    ebitda_usd_m: number;
    ebitda_margin_pct: number;
    net_income_usd_m: number;
    fcf_usd_m: number;
    eps: number;
  }>;                                // 4-5 years

  management: {
    ceo: string;                     // "Hock Tan, CEO since 2006"
    cfo?: string;
    insider_ownership_pct?: number;  // 0..100
    capital_allocation_history: string;  // 1-paragraph buybacks/dividends/M&A
    recent_insider_activity?: Array<{
      date: string;                  // "2026-02-06"
      insider: string;               // "Phillips Jason, President"
      action: string;                // "Sale" / "Buy" / "Option exercise"
      value_usd_m: number;           // positive = sale, negative = buy
    }>;
  };

  key_risks: Array<{
    category: "structural" | "cyclical" | "regulatory" | "execution" | "customer" | "macro";
    title: string;
    description: string;             // 50-100 words
  }>;                                // 5-7 entries

  social_sentiment?: {               // optional if Adanos/twitter-reader unavailable
    reddit_mentions?: number;        // weekly
    x_mentions?: number;
    bullish_pct?: number;            // 0..100
    summary: string;
  };
}
```

## Quality bar — "pitch-deck level"

For each section, depth matters:
- 8-10 bull catalysts, each with quantified evidence (numbers, dates, sources)
- 8-10 bear thesis-breakers, independently sourced
- 4-5 year historical P&L
- Segment-level revenue with growth rates and margins
- Top 3-5 customers if disclosed
- 5-7 key structural risks separate from bear thesis
- Management capital allocation history (buybacks, dividends, M&A)
- Recent insider activity (named transactions, $ values)
- Social sentiment summary

If a section's data isn't available from any skill, set the field to a minimal-but-valid value (empty array or short note) and add an entry to `data_gaps`. Do NOT use WebFetch to fill gaps.

## Mobile rendering hints

The output will render on a mobile-first Next.js page (max-width 880px). Plan accordingly:
- Use markdown tables in body text sparingly — most content is structured fields
- Keep titles in `bull_catalysts` / `bear_breakers` < 80 chars
- Numbers in `snapshot` should be presentation-ready strings (e.g., "$42.7B" not 42700000000)

## Return value to me (the orchestrator)

Just confirm in 1-3 lines:
- File written: `<absolute path>`
- Recommendation + 12m target in one sentence
- Number of bull catalysts / bear breakers
- Any major data gaps

Nothing else. I'll read the JSON.
