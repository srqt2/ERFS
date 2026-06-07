"""Banking valuation: Dividend Discount Model + Excess Returns Model + Justified P/B.

Banks should not be valued with DCF. Free cash flow is not meaningful for them
(loans are not capex, deposits are not working capital, growth is constrained by
regulatory equity not by asset reinvestment). The right frameworks are:

  1. Multi-stage Dividend Discount Model (DDM)
  2. Excess Returns Model: V = BV + PV of (ROE - Ke) * BV
  3. Justified P/B: P/B = (ROE - g) / (Ke - g)

Usage:
    python banking_ddm.py BBCA.JK
    python banking_ddm.py JPM --rf 0.044 --erp 0.055 --g_term 0.035

Output: JSON on stdout with the full triangulation.
"""

import argparse
import json
import sys

try:
    import yfinance as yf
except ImportError:
    sys.exit("ERROR: yfinance not installed. Run: pip install yfinance")


def fetch_inputs(ticker: str) -> dict:
    """Pull bank-relevant inputs from yfinance."""
    tk = yf.Ticker(ticker)
    info = tk.info

    dividends = tk.dividends
    if dividends is None or len(dividends) == 0:
        ttm_div = 0.0
    else:
        # Sum the last ~4 quarterly payments (or 1-2 annual for some markets)
        ttm_div = float(dividends.tail(4).sum())

    # Prefer the explicit dividend rate field if present
    declared = info.get("trailingAnnualDividendRate")
    if declared and declared > 0:
        ttm_div = float(declared)

    return {
        "ticker": ticker,
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "currency": info.get("currency") or "USD",
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
        "beta": info.get("beta") or 1.0,
        "trailing_eps": info.get("trailingEps"),
        "forward_eps": info.get("forwardEps"),
        "book_value_per_share": info.get("bookValue"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "return_on_equity": info.get("returnOnEquity"),
        "trailing_annual_dividend_rate": ttm_div,
        "trailing_annual_dividend_yield": info.get("trailingAnnualDividendYield"),
        "payout_ratio": info.get("payoutRatio"),
        "shares_outstanding": info.get("sharesOutstanding"),
    }


def compute_ke(beta: float, rf: float, erp: float) -> float:
    return rf + beta * erp


def multi_stage_ddm(d0, g_high, g_terminal, ke, years_high=5, years_decline=5):
    """Three-stage DDM: high growth, then linearly declining toward terminal, then perpetuity."""
    dividends = []
    d_t = d0

    # Stage 1: high growth
    for _ in range(years_high):
        d_t = d_t * (1 + g_high)
        dividends.append(d_t)

    # Stage 2: declining growth
    for t in range(1, years_decline + 1):
        g_t = g_high - (g_high - g_terminal) * t / years_decline
        d_t = d_t * (1 + g_t)
        dividends.append(d_t)

    pv_explicit = sum(d / (1 + ke) ** (i + 1) for i, d in enumerate(dividends))

    if ke <= g_terminal:
        terminal_value = float("inf")
    else:
        terminal_div = dividends[-1] * (1 + g_terminal)
        terminal_value = terminal_div / (ke - g_terminal)
    pv_terminal = terminal_value / (1 + ke) ** len(dividends)

    return {
        "implied_value": pv_explicit + pv_terminal,
        "pv_explicit_dividends": pv_explicit,
        "pv_terminal": pv_terminal,
        "terminal_value": terminal_value,
        "explicit_dividends": dividends,
    }


def excess_returns_model(bv0, roe, ke, g_book, horizon=10):
    """V = BV0 + PV of (ROE - Ke) * BV over horizon years."""
    bv = bv0
    pv_excess = 0.0
    path = []
    for t in range(1, horizon + 1):
        bv_prev = bv
        bv = bv * (1 + g_book)
        excess = (roe - ke) * bv_prev
        path.append(excess)
        pv_excess += excess / (1 + ke) ** t
    return {
        "implied_value": bv0 + pv_excess,
        "book_value_0": bv0,
        "pv_excess_returns": pv_excess,
        "excess_returns_path": path,
    }


def justified_pb(roe, g, ke):
    if ke <= g:
        return float("inf")
    return (roe - g) / (ke - g)


def sensitivity_matrix(d0, ke_range, g_range, years_high=5, years_decline=5):
    matrix = []
    for ke in ke_range:
        row = []
        for g in g_range:
            g_high_s = min(g + 0.05, 0.15)
            res = multi_stage_ddm(d0, g_high_s, g, ke, years_high, years_decline)
            v = res["implied_value"]
            row.append(round(v, 2) if v != float("inf") else None)
        matrix.append(row)
    return matrix


MAX_SUSTAINABLE_PAYOUT = 0.70  # Cap on payout for DDM normalization. Banks can't sustainably pay > ~70% over cycles.


def estimate_d0(inputs, override=None, max_payout=MAX_SUSTAINABLE_PAYOUT):
    """Normalized dividend per share for DDM.

    yfinance reports trailing dividend AND payout ratio both inflated when a
    bank paid a special dividend in the trailing window (BMRI 2024, BBNI 2024).
    Cap the effective payout at max_payout (default 70%) so the DDM models
    sustainable distributions rather than a one-off windfall.
    """
    if override is not None:
        return float(override), "user override"
    fwd_eps = inputs.get("forward_eps")
    trail_eps = inputs.get("trailing_eps")
    payout = inputs.get("payout_ratio")
    trail_div = inputs.get("trailing_annual_dividend_rate") or 0
    eps = fwd_eps if (fwd_eps and fwd_eps > 0) else trail_eps
    if eps and payout and payout > 0:
        effective_payout = min(payout, max_payout)
        d0 = eps * effective_payout
        note = f"forward EPS x effective payout ({eps:.2f} x {effective_payout:.0%})"
        if payout > max_payout:
            note += f"; raw payout {payout:.0%} capped at {max_payout:.0%} (suspect special div in TTM)"
        return d0, note
    if eps and trail_div:
        cap = eps * max_payout
        if trail_div > cap:
            return cap, f"trailing TTM div ({trail_div:.0f}) capped at {max_payout:.0%} of EPS ({cap:.0f})"
        return trail_div, "trailing TTM div"
    return trail_div, "trailing TTM div (no EPS cap available)"


def sanitize_beta(beta_raw, currency, ticker):
    """yfinance occasionally returns junk beta (negative or near-zero for
    newer / illiquid listings). Fall back to a sector default for banks."""
    if beta_raw is None:
        beta_raw = 1.0
    try:
        beta = float(beta_raw)
    except (TypeError, ValueError):
        beta = 1.0
    # IDX bank betas typically 0.9-1.3; US bank betas 0.9-1.4
    if abs(beta) < 0.3 or beta > 2.5:
        # Use sector default
        if currency in ("IDR", "INR", "PHP", "VND", "THB"):
            return 1.1, f"yfinance returned beta={beta} (suspect); using EM bank default 1.10"
        return 1.0, f"yfinance returned beta={beta} (suspect); using DM bank default 1.00"
    return beta, None


def value_bank(ticker, rf=None, erp=None, g_terminal=None, g_high=None, g_book=None, beta_override=None, d0_override=None):
    inputs = fetch_inputs(ticker)
    currency = inputs.get("currency") or "USD"

    # Currency-specific defaults
    defaults = {
        "IDR": dict(rf=0.0680, erp=0.065, g_term=0.045, g_high=0.10, g_book=0.10),
        "USD": dict(rf=0.0440, erp=0.055, g_term=0.035, g_high=0.08, g_book=0.08),
        "EUR": dict(rf=0.0250, erp=0.055, g_term=0.025, g_high=0.06, g_book=0.06),
        "SGD": dict(rf=0.0320, erp=0.055, g_term=0.030, g_high=0.07, g_book=0.07),
        "INR": dict(rf=0.0710, erp=0.065, g_term=0.050, g_high=0.12, g_book=0.12),
    }
    d = defaults.get(currency, defaults["USD"])

    rf = d["rf"] if rf is None else rf
    erp = d["erp"] if erp is None else erp
    g_terminal = d["g_term"] if g_terminal is None else g_terminal
    g_high = d["g_high"] if g_high is None else g_high
    g_book = d["g_book"] if g_book is None else g_book

    if beta_override is not None:
        beta = float(beta_override)
        beta_warning = None
    else:
        beta, beta_warning = sanitize_beta(inputs["beta"], currency, ticker)
    d0, d0_method = estimate_d0(inputs, override=d0_override)
    bv0 = inputs["book_value_per_share"] or 0.0
    roe = inputs["return_on_equity"] or 0.0
    current_price = inputs["current_price"] or 0.0
    current_pb = inputs["price_to_book"]

    ke = compute_ke(beta, rf, erp)

    ddm = multi_stage_ddm(d0, g_high, g_terminal, ke)
    er = excess_returns_model(bv0, roe, ke, g_book)
    jpb = justified_pb(roe, g_terminal, ke)
    jpb_value = jpb * bv0 if jpb != float("inf") else None

    # Sensitivity around the central Ke and g_terminal
    ke_range = [round(ke + delta, 4) for delta in [-0.02, -0.01, 0, 0.01, 0.02]]
    g_range = [round(g_terminal + delta, 4) for delta in [-0.02, -0.01, 0, 0.01, 0.02]]
    sens = sensitivity_matrix(d0, ke_range, g_range)

    # Blended 40/40/20 across the three methods (DDM/ER/JustifiedPB)
    components = []
    if ddm["implied_value"] != float("inf"):
        components.append(("ddm", ddm["implied_value"], 0.40))
    if er["implied_value"] != float("inf"):
        components.append(("er", er["implied_value"], 0.40))
    if jpb_value is not None and jpb_value != float("inf"):
        components.append(("jpb", jpb_value, 0.20))

    if components:
        total_w = sum(w for _, _, w in components)
        blended = sum(v * w for _, v, w in components) / total_w
    else:
        blended = None

    upside_pct = None
    if blended is not None and current_price:
        upside_pct = (blended - current_price) / current_price * 100

    return {
        "ticker": ticker,
        "company_name": inputs["company_name"],
        "currency": currency,
        "current_price": current_price,
        "current_pb": current_pb,
        "inputs": {
            "risk_free_rate_pct": round(rf * 100, 2),
            "equity_risk_premium_pct": round(erp * 100, 2),
            "beta": round(beta, 3),
            "current_dividend_per_share": round(d0, 4),
            "book_value_per_share": round(bv0, 2),
            "roe_pct": round(roe * 100, 2) if roe else None,
            "g_high_pct": round(g_high * 100, 2),
            "g_terminal_pct": round(g_terminal * 100, 2),
            "g_book_pct": round(g_book * 100, 2),
        },
        "cost_of_equity_pct": round(ke * 100, 2),
        "beta_warning": beta_warning,
        "d0_method": d0_method,
        "ddm": {
            "implied_value": round(ddm["implied_value"], 2),
            "pv_explicit": round(ddm["pv_explicit_dividends"], 2),
            "pv_terminal": round(ddm["pv_terminal"], 2),
            "explicit_dividends": [round(d, 2) for d in ddm["explicit_dividends"]],
        },
        "excess_returns": {
            "implied_value": round(er["implied_value"], 2),
            "book_value": round(er["book_value_0"], 2),
            "pv_excess": round(er["pv_excess_returns"], 2),
        },
        "justified_pb": {
            "ratio": round(jpb, 3) if jpb != float("inf") else None,
            "implied_value": round(jpb_value, 2) if jpb_value is not None else None,
        },
        "blended_implied_value": round(blended, 2) if blended is not None else None,
        "upside_pct": round(upside_pct, 1) if upside_pct is not None else None,
        "sensitivity_ke_x_g": {
            "ke_range_pct": [round(k * 100, 2) for k in ke_range],
            "g_range_pct": [round(g * 100, 2) for g in g_range],
            "matrix": sens,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bank valuation: DDM + Excess Returns + Justified P/B")
    parser.add_argument("ticker", help="Bank ticker (e.g., BBCA.JK, JPM)")
    parser.add_argument("--rf", type=float, default=None, help="Risk-free rate (decimal, e.g., 0.044)")
    parser.add_argument("--erp", type=float, default=None, help="Equity risk premium (decimal)")
    parser.add_argument("--g_term", type=float, default=None, help="Terminal growth (decimal)")
    parser.add_argument("--g_high", type=float, default=None, help="High-growth phase rate (decimal)")
    parser.add_argument("--g_book", type=float, default=None, help="Book value growth (decimal)")
    parser.add_argument("--beta", type=float, default=None, help="Override beta (otherwise yfinance + sanity check)")
    parser.add_argument("--d0", type=float, default=None, help="Override starting dividend per share (otherwise forward EPS x payout)")
    args = parser.parse_args()

    result = value_bank(
        args.ticker,
        rf=args.rf,
        erp=args.erp,
        g_terminal=args.g_term,
        g_high=args.g_high,
        g_book=args.g_book,
        beta_override=args.beta,
        d0_override=args.d0,
    )
    print(json.dumps(result, indent=2, default=str))
