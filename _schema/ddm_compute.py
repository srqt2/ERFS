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

# Default reasoning strings for each currency-specific assumption. Used when
# the LLM-supplied inputs don't include per-component reasoning. Goal: every
# assumption on the page has a justification, even auto-populated ones.
DEFAULT_REASONING = {
    "IDR": {
        "rf": "Indonesia 10Y government bond yield (~6.80% nominal). Bank Indonesia 7-day repo rate plus term premium.",
        "erp": "Indonesia equity risk premium (~6.5%). Damodaran 2025 country premium plus base ERP for emerging Asia.",
        "g_high": "10% high-growth period. Calibrated to historical IDX bank book-value CAGR over the last cycle.",
        "g_terminal": "4.5% terminal growth. Approximates Indonesia long-run nominal GDP (~5% nominal less ~0.5% productivity drag in financials).",
        "g_book": "10% book value compounding rate. Matches retained earnings x ROE for IDX BUKU-IV banks.",
    },
    "USD": {
        "rf": "US 10Y Treasury yield (~4.4% nominal).",
        "erp": "DM equity risk premium (~5.5%). Damodaran 2025 mature market premium.",
        "g_high": "8% high-growth period. Calibrated to DM bank earnings CAGR through a normalized cycle.",
        "g_terminal": "3.5% terminal growth. Approximates US long-run nominal GDP.",
        "g_book": "8% book value compounding rate. Retained earnings x ROE for DM banks.",
    },
    "EUR": {
        "rf": "Eurozone 10Y benchmark yield (~2.5% nominal, German Bund proxy).",
        "erp": "DM equity risk premium (~5.5%).",
        "g_high": "6% high-growth period for European banks.",
        "g_terminal": "2.5% terminal growth, approximating Eurozone long-run nominal GDP.",
        "g_book": "6% book value compounding rate.",
    },
    "SGD": {
        "rf": "Singapore 10Y SGS yield (~3.2% nominal).",
        "erp": "DM equity risk premium (~5.5%).",
        "g_high": "7% high-growth period for Singapore banks.",
        "g_terminal": "3% terminal growth, Singapore long-run nominal GDP.",
        "g_book": "7% book value compounding rate.",
    },
    "INR": {
        "rf": "India 10Y government bond yield (~7.1% nominal).",
        "erp": "India equity risk premium (~6.5%, Damodaran EM premium).",
        "g_high": "12% high-growth period. Calibrated to Indian bank book value CAGR.",
        "g_terminal": "5% terminal growth, India long-run nominal GDP (~6% nominal less productivity drag).",
        "g_book": "12% book value compounding rate.",
    },
}


def reasoning_for(currency: str, key: str, supplied: str = None) -> str:
    """Return the LLM-supplied reasoning if present; else the currency-specific default."""
    if supplied:
        return supplied
    bucket = DEFAULT_REASONING.get(currency, DEFAULT_REASONING.get("USD", {}))
    return bucket.get(key, "Auto-populated default; no specific reasoning supplied in inputs.")


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


def _compute_blended_px(rf, erp, beta, d0, bv0, roe, g_high, g_terminal, g_book,
                        years_high, years_decline, horizon_er):
    """Run DDM + Excess Returns + Justified P/B with one set of assumptions; return blended."""
    ke = rf + beta * erp
    ddm = multi_stage_ddm(d0, g_high, g_terminal, ke, years_high, years_decline)
    er = excess_returns_model(bv0, roe, ke, g_book, horizon_er)
    jpb_ratio = justified_pb(roe, g_terminal, ke)
    jpb_value = jpb_ratio * bv0 if jpb_ratio != float("inf") else None

    components = []
    if ddm["implied_value"] != float("inf"):
        components.append(("ddm", ddm["implied_value"], 0.40))
    if er["implied_value"] != float("inf"):
        components.append(("er", er["implied_value"], 0.40))
    if jpb_value is not None and jpb_value != float("inf"):
        components.append(("jpb", jpb_value, 0.20))
    blended = sum(v * w for _, v, w in components) / sum(w for _, _, w in components) if components else None

    return {
        "ke": ke,
        "ddm": ddm,
        "er": er,
        "jpb_ratio": jpb_ratio,
        "jpb_value": jpb_value,
        "blended": blended,
    }


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

    base = _compute_blended_px(rf, erp, beta, d0, bv0, roe, g_high, g_terminal, g_book,
                               years_high, years_decline, horizon_er)
    ke = base["ke"]
    ddm = base["ddm"]
    er = base["er"]
    jpb_ratio = base["jpb_ratio"]
    jpb_value = base["jpb_value"]
    blended = base["blended"]

    ke_range = [round(ke + d, 4) for d in [-0.02, -0.01, 0.0, 0.01, 0.02]]
    g_range = [round(g_terminal + d, 4) for d in [-0.02, -0.01, 0.0, 0.01, 0.02]]
    sens = sensitivity_matrix(d0, ke_range, g_range, years_high, years_decline)

    base_implied_px = round(blended, 2) if blended is not None else None
    upside_pct = (blended - current_price) / current_price * 100 if (blended and current_price) else None

    # Per-component reasoning. Use LLM-supplied if present in `overrides.reasonings`, else defaults.
    supplied_reasonings = overrides.get("reasonings", {}) if isinstance(overrides.get("reasonings"), dict) else {}

    inputs_for_output = {
        "rf": round(rf, 4),
        "rf_reasoning": reasoning_for(currency, "rf", supplied_reasonings.get("rf")),
        "erp": round(erp, 4),
        "erp_reasoning": reasoning_for(currency, "erp", supplied_reasonings.get("erp")),
        "beta": round(beta, 3),
        "beta_reasoning": beta_warning or supplied_reasonings.get("beta") or "yfinance equity beta vs local index.",
        "beta_warning": beta_warning,
        "d0": round(d0, 4),
        "d0_method": d0_method,
        "d0_reasoning": supplied_reasonings.get("d0") or f"Sustainable dividend per share. {d0_method}.",
        "book_value_per_share": round(bv0, 2),
        "book_value_reasoning": supplied_reasonings.get("book_value_per_share") or "Book value per share from yfinance (most recent reported balance sheet).",
        "roe": round(roe, 4),
        "roe_reasoning": supplied_reasonings.get("roe") or "Return on equity from yfinance (TTM).",
        "g_high": round(g_high, 4),
        "g_high_reasoning": reasoning_for(currency, "g_high", supplied_reasonings.get("g_high")),
        "g_terminal": round(g_terminal, 4),
        "g_terminal_reasoning": reasoning_for(currency, "g_terminal", supplied_reasonings.get("g_terminal")),
        "g_book": round(g_book, 4),
        "g_book_reasoning": reasoning_for(currency, "g_book", supplied_reasonings.get("g_book")),
        "years_high": years_high,
        "years_decline": years_decline,
        "horizon_excess_returns": horizon_er,
        "max_payout_cap": max_payout,
        "reasoning": spec.get("primary_method", {}).get("reasoning", ""),
    }

    # Build calculation_trace showing how each derived number was computed.
    ke_calc = {
        "formula": "Ke = Rf + Beta x ERP  (CAPM)",
        "steps": [
            {"label": "Rf", "expression": "input", "result": f"{rf:.4f} ({rf*100:.2f}%)"},
            {"label": "Beta", "expression": "input", "result": f"{beta:.3f}"},
            {"label": "ERP", "expression": "input", "result": f"{erp:.4f} ({erp*100:.2f}%)"},
            {"label": "Beta x ERP", "expression": f"{beta:.3f} x {erp:.4f}", "result": f"{beta*erp:.4f}"},
            {"label": "Ke", "expression": f"{rf:.4f} + {beta*erp:.4f}", "result": f"{ke:.4f} ({ke*100:.2f}%)"},
        ],
    }
    ddm_calc = {
        "formula": "DDM value = sum(D_t / (1+Ke)^t) + (D_N x (1+g_term) / (Ke - g_term)) / (1+Ke)^N",
        "steps": [
            {"label": "Years high growth", "expression": "input", "result": str(years_high)},
            {"label": "Years declining", "expression": "input", "result": str(years_decline)},
            {"label": "PV of explicit dividends", "expression": f"sum over {years_high + years_decline} years", "result": f"{ddm['pv_explicit']:.2f}"},
            {"label": "Terminal dividend", "expression": f"{ddm['explicit_dividends'][-1]:.2f} x (1 + {g_terminal:.4f})", "result": f"{ddm['explicit_dividends'][-1] * (1 + g_terminal):.2f}"},
            {"label": "Terminal value", "expression": f"{ddm['explicit_dividends'][-1] * (1 + g_terminal):.2f} / ({ke:.4f} - {g_terminal:.4f})", "result": f"{ddm['terminal_value']:.2f}" if ddm['terminal_value'] != float('inf') else "n/a"},
            {"label": "PV of terminal value", "expression": f"{ddm['terminal_value']:.2f} / (1+{ke:.4f})^{years_high + years_decline}" if ddm['terminal_value'] != float('inf') else "n/a", "result": f"{ddm['pv_terminal']:.2f}" if ddm['pv_terminal'] != float('inf') else "n/a"},
            {"label": "DDM value", "expression": f"{ddm['pv_explicit']:.2f} + {ddm['pv_terminal']:.2f}", "result": f"{ddm['implied_value']:.2f}"},
        ],
    }
    er_calc = {
        "formula": "Excess Returns value = BV_0 + sum( (ROE - Ke) x BV_t / (1+Ke)^t )",
        "steps": [
            {"label": "Book value 0", "expression": "input", "result": f"{er['book_value_0']:.2f}"},
            {"label": "ROE - Ke (annual spread)", "expression": f"{roe:.4f} - {ke:.4f}", "result": f"{roe - ke:.4f}"},
            {"label": "PV of excess returns (over horizon)", "expression": f"sum over {horizon_er} years", "result": f"{er['pv_excess']:.2f}"},
            {"label": "Implied value", "expression": f"{er['book_value_0']:.2f} + {er['pv_excess']:.2f}", "result": f"{er['implied_value']:.2f}"},
        ],
    }
    jpb_calc = {
        "formula": "Justified P/B = (ROE - g) / (Ke - g);  Implied value = Justified P/B x Book value",
        "steps": [
            {"label": "ROE - g_term", "expression": f"{roe:.4f} - {g_terminal:.4f}", "result": f"{roe - g_terminal:.4f}"},
            {"label": "Ke - g_term", "expression": f"{ke:.4f} - {g_terminal:.4f}", "result": f"{ke - g_terminal:.4f}"},
            {"label": "Justified P/B ratio", "expression": f"{roe - g_terminal:.4f} / {ke - g_terminal:.4f}", "result": f"{jpb_ratio:.3f}x" if jpb_ratio != float('inf') else "n/a"},
            {"label": "Implied value", "expression": f"{jpb_ratio:.3f} x {bv0:.2f}" if jpb_ratio != float('inf') else "n/a", "result": f"{jpb_value:.2f}" if jpb_value is not None else "n/a"},
        ],
    }
    blend_calc = {
        "formula": "Blended value = (0.40 x DDM) + (0.40 x ER) + (0.20 x Justified P/B)",
        "steps": [
            {"label": "DDM x 40%", "expression": f"0.40 x {ddm['implied_value']:.2f}", "result": f"{0.40 * ddm['implied_value']:.2f}"},
            {"label": "ER x 40%", "expression": f"0.40 x {er['implied_value']:.2f}", "result": f"{0.40 * er['implied_value']:.2f}"},
            {"label": "Justified P/B x 20%", "expression": f"0.20 x {jpb_value:.2f}" if jpb_value is not None else "n/a", "result": f"{0.20 * jpb_value:.2f}" if jpb_value is not None else "n/a"},
            {"label": "Blended", "expression": "sum of contributions", "result": f"{blended:.2f}" if blended is not None else "n/a"},
        ],
    }
    primary_trace = {
        "ke": ke_calc,
        "ddm": ddm_calc,
        "excess_returns": er_calc,
        "justified_pb": jpb_calc,
        "blended": blend_calc,
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
        "blended_implied_value": base_implied_px,
        "implied_px": base_implied_px,
    }

    # ===== SCENARIOS =====
    # If spec supplies scenarios with key_changes (Ke, ROE, g_high, g_terminal, etc.),
    # run the full stack for each scenario and emit implied_px + probability + reasoning.
    spec_scenarios = spec.get("scenarios")
    scenarios_out = []
    if spec_scenarios:
        for scen in spec_scenarios:
            kc = scen.get("key_changes", {})
            # Allow scenario-level overrides of any pricing input
            s_ke = kc.get("ke")  # if Ke supplied directly, use it (recompute beta back-solving below)
            s_rf = kc.get("rf", rf)
            s_erp = kc.get("erp", erp)
            s_beta = kc.get("beta", beta)
            s_roe = kc.get("roe", roe)
            s_g_high = kc.get("g_high", g_high)
            s_g_terminal = kc.get("g_terminal", g_terminal)
            s_g_book = kc.get("g_book", g_book)
            s_d0 = kc.get("d0", d0)
            s_bv = kc.get("book_value_per_share", bv0)

            # If Ke is set directly, solve for an implied beta so CAPM still holds
            if s_ke is not None:
                if s_erp > 0:
                    s_beta_eff = (s_ke - s_rf) / s_erp
                else:
                    s_beta_eff = s_beta
                s_res = _compute_blended_px(s_rf, s_erp, s_beta_eff, s_d0, s_bv, s_roe,
                                            s_g_high, s_g_terminal, s_g_book,
                                            years_high, years_decline, horizon_er)
            else:
                s_res = _compute_blended_px(s_rf, s_erp, s_beta, s_d0, s_bv, s_roe,
                                            s_g_high, s_g_terminal, s_g_book,
                                            years_high, years_decline, horizon_er)

            s_blended = s_res["blended"]
            scenarios_out.append({
                "label": scen["label"],
                "key_changes": kc,
                "implied_px": round(s_blended, 2) if s_blended is not None else None,
                "probability": scen.get("probability"),
                "probability_reasoning": scen.get("probability_reasoning", ""),
                "reasoning": scen.get("reasoning", ""),
                "ddm_implied_value": round(s_res["ddm"]["implied_value"], 2)
                                     if s_res["ddm"]["implied_value"] != float("inf") else None,
                "excess_returns_implied_value": round(s_res["er"]["implied_value"], 2),
                "justified_pb_ratio": round(s_res["jpb_ratio"], 3)
                                      if s_res["jpb_ratio"] != float("inf") else None,
                "justified_pb_implied_value": round(s_res["jpb_value"], 2)
                                              if s_res["jpb_value"] is not None else None,
                "cost_of_equity_pct": round(s_res["ke"] * 100, 2),
            })
    else:
        scenarios_out = [
            {"label": "Bear", "key_changes": {"roe_compression": "-300bps"}, "implied_px": None, "reasoning": "ROE compression from NIM drop"},
            {"label": "Base", "key_changes": {}, "implied_px": base_implied_px, "reasoning": "Central assumptions"},
            {"label": "Bull", "key_changes": {"roe_expansion": "+200bps"}, "implied_px": None, "reasoning": "ROE expansion + lower Ke"},
        ]

    # ===== BLEND =====
    # New v3 protocol: probability-weighted scenarios + optional cross_check weight
    cc_spec = spec.get("cross_check")
    cross_check_out = None
    if cc_spec:
        cc_px = cc_spec.get("inputs", {}).get("implied_px", current_price)
        cross_check_out = {
            "name": cc_spec.get("name", "Current P/B"),
            "category": cc_spec.get("category", "relative"),
            "reasoning": cc_spec.get("reasoning", "Market-implied P/B cross-check"),
            "inputs": cc_spec.get("inputs", {"current_pb": current_pb}),
            "outputs": {"implied_px": cc_px},
        }
    elif current_pb:
        cross_check_out = {
            "name": "Current P/B",
            "category": "relative",
            "reasoning": "Cross-check against market-implied P/B",
            "inputs": {"current_pb": current_pb},
            "outputs": {"implied_px": current_price},
        }

    blending_weights = spec.get("blending_weights", {})
    cc_weight = blending_weights.get("cross_check", 0)

    weighted_contribs = []
    scenario_weight_total = 0.0
    for s in scenarios_out:
        prob = s.get("probability")
        if prob is None or s.get("implied_px") is None:
            continue
        contrib = prob * s["implied_px"]
        weighted_contribs.append({
            "component": s["label"],
            "implied_px": s["implied_px"],
            "weight": prob,
            "contribution": round(contrib, 2),
            "reasoning": s.get("probability_reasoning", ""),
        })
        scenario_weight_total += prob

    if cross_check_out and cc_weight > 0:
        cc_px = cross_check_out["outputs"]["implied_px"]
        contrib = cc_weight * cc_px
        weighted_contribs.append({
            "component": cross_check_out["name"],
            "implied_px": cc_px,
            "weight": cc_weight,
            "contribution": round(contrib, 2),
            "reasoning": cross_check_out.get("reasoning", ""),
        })

    if weighted_contribs:
        blended_target = round(sum(c["contribution"] for c in weighted_contribs), 2)
        blending_logic = spec.get("blending_logic",
            "Σ(scenario.probability × scenario.implied_px) + cross_check_weight × cross_check.implied_px")
    else:
        # Fall back to legacy 40/40/20 component blend
        blended_target = base_implied_px
        blending_logic = spec.get("blending_logic",
            "DDM x 40% + Excess Returns x 40% + Justified P/B x 20%")
        blending_weights = blending_weights or {"ddm": 0.40, "excess_returns": 0.40, "justified_pb": 0.20}

    weight_total = scenario_weight_total + cc_weight
    final_upside_pct = ((blended_target - current_price) / current_price * 100
                       if (blended_target is not None and current_price) else None)

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
            "calculation_trace": primary_trace,
            "sensitivity": {
                "rows_label": "Ke",
                "cols_label": "Terminal g",
                "row_values": ke_range,
                "col_values": g_range,
                "implied_px_matrix": sens,
            },
        },
        "cross_check": cross_check_out,
        "scenarios": scenarios_out,
        "blended_target": blended_target,
        "blending_logic": blending_logic,
        "blending_weights": blending_weights,
        "weights_reasoning": spec.get("weights_reasoning", ""),
        "weighted_contributions": weighted_contribs,
        "weight_total_check": round(weight_total, 4) if weighted_contribs else None,
        "upside_pct": round(final_upside_pct, 1) if final_upside_pct is not None else None,
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
