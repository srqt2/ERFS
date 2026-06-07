---
name: banking-ddm
description: Bank valuation via Dividend Discount Model (DDM), Excess Returns Model, and Justified P/B ratio. Use whenever the user asks about valuing a bank or other capital-constrained financial institution, or when a bank ticker comes up in a valuation context. Triggers include - "value this bank", "DDM for BBCA", "fair P/B for JPM", "what's BBNI worth", "two-stage DDM", "Gordon growth model", "excess returns model", "justified P/B", "cost of equity for bank". Bank tickers like BBCA, BBRI, BMRI, BBNI, BBTN, BRIS, JPM, BAC, WFC, C, GS, MS, HSBC, DBS, OCBC, UOB, ICICI, AXIS, KOTAK, HDFC, SBI, CBA, NAB, WBC, ANZ in a valuation context. Banks should NOT be valued with DCF since free cash flow is not meaningful for them. Always use this skill for bank valuation instead of company-valuation (which uses DCF).
---

# Banking Dividend Discount Model

Banks can't be valued with DCF. Free cash flow isn't meaningful for them: loans aren't capex, deposits aren't working capital, and growth is constrained by regulatory equity capital (CAR / CET1), not by asset reinvestment.

Use three parallel frameworks instead:

1. **Multi-stage Dividend Discount Model (DDM)** - explicit dividends for years 1-5 (high growth), 5 years declining toward terminal, then Gordon Growth perpetuity
2. **Excess Returns Model** - V = BV + PV of (ROE - Ke) x BV. Isolates value-add above book equity. If ROE > Ke, premium to book.
3. **Justified P/B** - (ROE - g) / (Ke - g). Sanity-checks the appropriate P/B given returns vs cost of capital.

## How to use

Run `ddm.py` in this folder with the bank's ticker (use `.JK` suffix for IDX, `.NS` for NSE India, `.SI` for Singapore, etc):

```bash
python ddm.py BBCA.JK
python ddm.py JPM
python ddm.py DBS.SI
```

The script auto-detects currency from yfinance and uses currency-specific default assumptions. Override any input via CLI flags:

```bash
python ddm.py BBNI.JK --rf 0.0680 --erp 0.065 --g_term 0.045 --beta 1.10
python ddm.py JPM --d0 4.20 --g_high 0.07
```

## Output (JSON to stdout)

- **inputs**: rf, ERP, beta, D0, BV, ROE, growth assumptions
- **cost_of_equity_pct**: Ke via CAPM
- **ddm**: explicit dividends + PV explicit + PV terminal + implied value
- **excess_returns**: book value + PV excess + implied value
- **justified_pb**: ratio + implied value
- **blended_implied_value**: 40/40/20 weighted across the three methods
- **upside_pct**: vs current price
- **sensitivity_ke_x_g**: matrix of implied values across Ke (±2%) and g_terminal (±2%)
- **beta_warning**: present if yfinance returned a suspect beta (negative, near-zero, > 2.5) and the script substituted a sector default
- **d0_method**: how the dividend was normalized. Flags if a special dividend was filtered out via the 70% payout cap.

## Why a 70% payout cap

yfinance reports trailing dividend AND payout ratio inflated when a bank paid a special dividend in the trailing window (BMRI 2024, BBNI 2024 are classic examples). The script caps the effective payout at 70% so the DDM models a sustainable distribution, not a one-off windfall. Override via `--max_payout 0.85` if a bank genuinely sustains a higher payout.

## Currency defaults

| Currency | Rf | ERP | g_terminal | g_high |
|---|---|---|---|---|
| IDR | 6.80% | 6.50% | 4.50% | 10.00% |
| USD | 4.40% | 5.50% | 3.50% | 8.00% |
| EUR | 2.50% | 5.50% | 2.50% | 6.00% |
| SGD | 3.20% | 5.50% | 3.00% | 7.00% |
| INR | 7.10% | 6.50% | 5.00% | 12.00% |

For any other currency, the script falls back to USD defaults. Override via the CLI flags if you need country-specific calibration.

## When to skip this skill

- Insurance / asset managers - use embedded value or AUM-based frameworks instead
- Distressed banks with suspended dividends - DDM breaks down at D0 = 0
- Newly listed banks with no dividend history - use price-to-book peer median instead
- Non-bank companies - use `finance-market-analysis:company-valuation` (DCF + multiples + SOTP)

## Interpreting the three numbers

The three methods usually triangulate within 15-30% of each other:

- **DDM higher than ER / JPB**: bank pays out aggressively today, less reinvestment for compounding. Premium to BV justified by current income, not by book growth.
- **ER higher than DDM**: bank reinvests heavily (low payout), book compounds at high ROE. Future excess returns deliver more value than current dividends.
- **JPB much higher**: market is pricing in lower long-term growth than the maths imply.
- **All three converge**: ROE is sustainably above Ke and the bank distributes some of that as dividends. Healthy.
- **All three below current price**: bank is trading at a premium for franchise quality, brand, or scarcity. The math doesn't support it.

Cite all three numbers and the blended target in your report. Don't pick one in isolation.
