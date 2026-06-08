"""DDM / Excess Returns / Justified P/B valuation engine for banks.

LLM picks the assumptions (rf, ERP, beta, growth path, payout) and yfinance
supplies anchor data. Python does the math. Outputs valuation JSON in the
canonical shape (see VALUATION_SCHEMA.md).

Two ways to call:

  # Quick path - just supply a ticker, script pulls everything from yfinance
  python ddm_compute.py --ticker BBCA.JK --output output/BBCA/BBCA_valuation.json

  # Override path - LLM-supplied inputs override yfinance defaults
  python ddm_compute.py --inputs output/BBCA/BBCA_inputs.json --output output/BBCA/BBCA_valuation.json

The override path is preferred when the LLM has done sector / country reasoning
on top of raw yfinance defaults.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    sys.exit("ERROR: yfinance not installed. Run: pip install yfinance")


MAX_SUSTAINABLE_PAYOUT = 0.70

CURRENCY_DEFAULTS = {
    "IDR": dict(rf=0.0680, erp=0.065, g_term=0.045, g_high=0.10, g_book=0.10, sector_beta=1.10),
    "USD": dict(rf=0.0440, erp=0.055, g_term=0.035, g_high=0.08, g_book=0.08, sector_beta=1.00),
    "EUR": dict(rf=0.0250, erp=0.055, g_term=0.025, g_high=0.06, g_book=0.06, sector_beta=1.00),
    "SGD": dict(rf=0.0320, erp=0.055, g_term=0.030, g_high=0.07, g_book=0.07, sector_beta=1.00),
    "INR": dict(rf=0.0710, erp=0.065, g_term=0.050, g_high=0.12, g_book=0.12, sector_beta=1.10),
}


def fetch_inputs(ticker: str) -> dict:
    tk = yf.Ticker(ticker)
    info = tk.info
    dividends = tk.dividends
    ttm_div = float(dividends.tail(4).sum()) if dividends is not None and len(dividends) else 0.0
    declared = info.get("trailingAnnualDividendRate")
    if declared and declared > 0:
        ttm_div = float(declared)
    return {
        "ticker": ticker,
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "currency": info.get("currency") or "USD",
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "beta_raw": info.get("beta") or 1.0,
        "trailing_eps": info.get("trailingEps"),
        "forward_eps": info.get("forwardEps"),
        "book_value_per_share": info.get("bookValue"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "return_on_equity": info.get("returnOnEquity"),
        "trailing_annual_dividend_rate": ttm_div,
        "payout_ratio": info.get("payoutRatio"),
    }


def sanitize_beta(beta_raw, currency):
    try:
        beta = float(beta_raw) if beta_raw is not None else 1.0
    except (TypeError, ValueError):
        beta = 1.0
    if abs(beta) < 0.3 or beta > 2.5:
        sector_default = CURRENCY_DEFAULTS.get(currency, CURRENCY_DEFAULTS["USD"])["sector_beta"]
        return sector_default, f"yfinance returned beta={beta_raw} (suspect); using sector default {sector_default}"
    return beta, None


def estimate_d0(inputs, override=None, max_payout=MAX_SUSTAINABLE_PAYOUT):
    if override is not None:
        return float(override), "user override"
    fwd_eps = inputs.get("forward_eps")
    trail_eps = inputs.get("trailing_eps")
    payout = inputs.get("payout_ratio")
    trail_div = inputs.get("trailing_annual_dividend_rate") or 0
    eps = fwd_eps if (fwd_eps and fwd_eps > 0) else trail_eps
    if eps and payout and payout > 0:
        effective = min(payout, max_payout)
        d0 = eps * effective
        note = f"forward EPS x effective payout ({eps:.2f} x {effective:.0%})"
        if payout > max_payout:
            note += f"; raw payout {payout:.0%} capped at {max_payout:.0%} (likely contained special)"
        return d0, note
    if eps and trail_div:
        cap = eps * max_payout
        if trail_div > cap:
            return cap, f"trailing TTM div ({trail_div:.0f}) capped at {max_payout:.0%} of EPS ({cap:.0f})"
        return trail_div, "trailing TTM div"
    return trail_div, "trailing TTM div (no EPS cap available)"


def multi_stage_ddm(d0, g_high, g_terminal, ke, years_high=5, years_decline=5):
    divs = []
    d_t = d0
    for _ in range(years_high):
        d_t *= (1 + g_high)
        divs.append(d_t)
    for t in range(1, years_decline + 1):
        g_t = g_high - (g_high - g_terminal) * t / years_decline
        d_t *= (1 + g_t)
        divs.append(d_t)
    pv_explicit = sum(d / (1 + ke) ** (i + 1) for i, d in enumerate(divs))
    if ke <= g_terminal:
        tv = float("inf"); pv_term = float("inf")
    else:
        term_div = divs[-1] * (1 + g_terminal)
        tv = term_div / (ke - g_terminal)
        pv_term = tv / (1 + ke) ** len(divs)
    return {
        "implied_value": pv_explicit + pv_term,
        "pv_explicit": pv_explicit,
        "pv_terminal": pv_term,
        "terminal_value": tv,
        "explicit_dividends": divs,
    }


def excess_returns_model(bv0, roe, ke, g_book, horizon=10):
    bv = bv0
    pv_excess = 0.0
    path = []
    for t in range(1, horizon + 1):
        bv_prev = bv
        bv *= (1 + g_book)
        excess = (roe - ke) * bv_prev
        path.append(excess)
        pv_excess += excess / (1 + ke) ** t
    return {"implied_value": bv0 + pv_excess, "book_value_0": bv0, "pv_excess": pv_excess, "excess_path": path}


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


def value_bank_from_inputs(spec: dict) -> dict:
    """spec is the LLM-supplied inputs JSON; we top up with yfinance defaults."""
    ticker = spec["ticker"]
    raw = fetch_inputs(ticker)
    currency = spec.get("currency") or raw["currency"] or "USD"
    defaults = CURRENCY_DEFAULTS.get(currency, CURRENCY_DEFAULTS["USD"])

    overrides = spec.get("primary_method", {}).get("inputs", {})

    rf = overrides.get("rf", defaults["rf"])
    erp = overrides.get("erp", defaults["erp"])
    beta_override = overrides.get("beta")
    g_high = overrides.get("g_high", defaults["g_high"])
    g_terminal = overrides.get("g_terminal", defaults["g_term"])
    g_book = overrides.get("g_book", defaults["g_book"])
    years_high = overrides.get("years_high", 5)
    years_decline = overrides.get("years_decline", 5)
    horizon_er = overrides.get("horizon_excess_returns", 10)
    max_payout = overrides.get("max_payout_cap", MAX_SUSTAINABLE_PAYOUT)

    if beta_override is not None:
        beta = float(beta_override)
        beta_warning = None
    else:
        beta, beta_warning = sanitize_beta(raw["beta_raw"], currency)

    d0_override = overrides.get("d0")
    d0, d0_method = estimate_d0(raw, override=d0_override, max_payout=max_payout)

    bv0 = overrides.get("book_value_per_share", raw["book_value_per_share"] or 0.0)
    roe = overrides.get("roe", raw["return_on_equity"] or 0.0)
    current_price = spec.get("current_price") or raw["current_price"] or 0.0
    current_pb = raw["price_to_book"]

    ke = rf + beta * erp

    ddm = multi_stage_ddm(d0, g_high, g_terminal, ke, years_high, years_decline)
    er = excess_returns_model(bv0, roe, ke, g_book, horizon_er)
    jpb_ratio = justified_pb(roe, g_terminal, ke)
    jpb_value = jpb_ratio * bv0 if jpb_ratio != float("inf") else None

    ke_range = [round(ke + d, 4) for d in [-0.02, -0.01, 0.0, 0.01, 0.02]]
    g_range = [round(g_terminal + d, 4) for d in [-0.02, -0.01, 0.0, 0.01, 0.02]]
    sens = sensitivity_matrix(d0, ke_range, g_range, years_high, years_decline)

    components = []
    if ddm["implied_value"] != float("inf"):
        components.append(("ddm", ddm["implied_value"], 0.40))
    if er["implied_value"] != float("inf"):
        components.append(("er", er["implied_value"], 0.40))
    if jpb_value is not None and jpb_value != float("inf"):
        components.append(("jpb", jpb_value, 0.20))
    blended = sum(v * w for _, v, w in components) / sum(w for _, _, w in components) if components else None

    upside_pct = (blended - current_price) / current_price * 100 if (blended and current_price) else None

    inputs_for_output = {
        "rf": round(rf, 4),
        "erp": round(erp, 4),
        "beta": round(beta, 3),
        "beta_warning": beta_warning,
        "d0": round(d0, 4),
        "d0_method": d0_method,
        "book_value_per_share": round(bv0, 2),
        "roe": round(roe, 4),
        "g_high": round(g_high, 4),
        "g_terminal": round(g_terminal, 4),
        "g_book": round(g_book, 4),
        "years_high": years_high,
        "years_decline": years_decline,
        "horizon_excess_returns": horizon_er,
        "max_payout_cap": max_payout,
        "reasoning": spec.get("primary_method", {}).get("reasoning", ""),
    }

    outputs = {
        "cost_of_equity_pct": round(ke * 100, 2),
        "ddm": {
            "implied_value": round(ddm["implied_value"], 2),
            "pv_explicit": round(ddm["pv_explicit"], 2),
            "pv_terminal": round(ddm["pv_terminal"], 2),
            "explicit_dividends": [round(d, 2) for d in ddm["explicit_dividends"]],
        },
        "excess_returns": {
            "implied_value": round(er["implied_value"], 2),
            "book_value": round(er["book_value_0"], 2),
            "pv_excess": round(er["pv_excess"], 2),
        },
        "justified_pb": {
            "ratio": round(jpb_ratio, 3) if jpb_ratio != float("inf") else None,
            "implied_value": round(jpb_value, 2) if jpb_value is not None else None,
        },
        "blended_implied_value": round(blended, 2) if blended is not None else None,
        "implied_px": round(blended, 2) if blended is not None else None,
    }

    valuation = {
        "schema_version": "1.0",
        "ticker": ticker,
        "currency": currency,
        "current_price": current_price,
        "shares_outstanding_b": (raw["shares_outstanding"] or 0) / 1e9,
        "net_debt_b": 0,
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "primary_method": {
            "name": "DDM",
            "category": "intrinsic",
            "reasoning": spec.get("primary_method", {}).get("reasoning",
                "Banks should not be valued with DCF since FCF is not meaningful. DDM + Excess Returns + Justified P/B is the standard banking framework."),
            "inputs": inputs_for_output,
            "outputs": outputs,
            "sensitivity": {
                "rows_label": "Ke",
                "cols_label": "Terminal g",
                "row_values": ke_range,
                "col_values": g_range,
                "implied_px_matrix": sens,
            },
        },
        "cross_check": {
            "name": "Current P/B",
            "category": "relative",
            "reasoning": "Cross-check against market-implied P/B",
            "inputs": {"current_pb": current_pb},
            "outputs": {"implied_px": current_price},
        } if current_pb else None,
        "scenarios": [
            {"label": "Bear", "key_changes": {"roe_compression": "-300bps"}, "implied_px": None, "reasoning": "ROE compression from NIM drop"},
            {"label": "Base", "key_changes": {}, "implied_px": outputs["implied_px"], "reasoning": "Central assumptions"},
            {"label": "Bull", "key_changes": {"roe_expansion": "+200bps"}, "implied_px": None, "reasoning": "ROE expansion + lower Ke"},
        ],
        "blended_target": outputs["implied_px"],
        "blending_logic": "DDM x 40% + Excess Returns x 40% + Justified P/B x 20%",
        "blending_weights": {"ddm": 0.40, "excess_returns": 0.40, "justified_pb": 0.20},
        "upside_pct": round(upside_pct, 1) if upside_pct is not None else None,
    }
    return valuation


def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticker", help="Quick mode: just supply a ticker; script pulls everything from yfinance")
    g.add_argument("--inputs", help="Override mode: path to inputs JSON with LLM-chosen overrides")
    parser.add_argument("--output", required=True, help="Path to output valuation JSON")
    args = parser.parse_args()

    if args.ticker:
        spec = {"ticker": args.ticker}
    else:
        with open(args.inputs, encoding="utf-8") as f:
            spec = json.load(f)

    valuation = value_bank_from_inputs(spec)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(valuation, f, indent=2, ensure_ascii=False)

    print(f"[OK] wrote {out_path}")
    p = valuation["primary_method"]
    print(f"  Ke = {p['outputs']['cost_of_equity_pct']}%  beta = {p['inputs']['beta']}")
    print(f"  DDM = {p['outputs']['ddm']['implied_value']}")
    print(f"  Excess Returns = {p['outputs']['excess_returns']['implied_value']}")
    print(f"  Justified P/B = {p['outputs']['justified_pb']['implied_value']}")
    print(f"  Blended target = {valuation['blended_target']}  ({valuation['upside_pct']:+.1f}%)")


if __name__ == "__main__":
    main()
