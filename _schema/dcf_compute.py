"""DCF valuation engine. LLM picks the inputs; Python does the math.

Usage:
    python dcf_compute.py --inputs avgo_inputs.json --output avgo_valuation.json

The inputs JSON contains every assumption (WACC components, terminal growth, FCF
projections, scenarios, optional cross-check). Output JSON matches
VALUATION_SCHEMA.md so render_excel.py and the Next.js valuation page can render
it deterministically.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def build_fcff_decomposition(year_data: dict) -> dict:
    """Given a single year's projection inputs, derive the full FCFF build:
    Revenue -> EBIT -> NOPAT -> D&A -> Capex -> dNWC -> FCFF.

    Backwards compatible: if year_data only carries `fcf_b`, the decomposition
    fields are returned as None (caller decides how to display).
    """
    revenue = year_data.get("revenue_b")
    ebit_margin = year_data.get("ebit_margin")
    tax_rate = year_data.get("tax_rate", 0.21)
    da_pct = year_data.get("da_pct_of_revenue")
    capex_pct = year_data.get("capex_pct_of_revenue")
    wc_pct = year_data.get("wc_change_pct_of_revenue")

    if revenue is not None and ebit_margin is not None:
        ebit = revenue * ebit_margin
        nopat = ebit * (1 - tax_rate)
        da = revenue * da_pct if da_pct is not None else None
        capex = revenue * capex_pct if capex_pct is not None else None
        wc_change = revenue * wc_pct if wc_pct is not None else None
        fcff_derived = None
        if da is not None and capex is not None and wc_change is not None:
            fcff_derived = nopat + da - capex - wc_change
        return {
            "revenue_b": round(revenue, 3),
            "ebit_margin": ebit_margin,
            "ebit_b": round(ebit, 3),
            "tax_rate": tax_rate,
            "nopat_b": round(nopat, 3),
            "da_b": round(da, 3) if da is not None else None,
            "da_pct": da_pct,
            "capex_b": round(capex, 3) if capex is not None else None,
            "capex_pct": capex_pct,
            "wc_change_b": round(wc_change, 3) if wc_change is not None else None,
            "wc_pct": wc_pct,
            "fcff_derived_b": round(fcff_derived, 3) if fcff_derived is not None else None,
            "has_full_build": all(x is not None for x in [da, capex, wc_change]),
        }
    return {
        "revenue_b": revenue,
        "ebit_margin": ebit_margin,
        "ebit_b": None,
        "tax_rate": tax_rate,
        "nopat_b": None,
        "da_b": None,
        "capex_b": None,
        "wc_change_b": None,
        "fcff_derived_b": None,
        "has_full_build": False,
    }


def compute_dual_terminal_value(last_fcf: float, terminal_g: float, wacc: float,
                                 horizon: int, last_ebitda_b: float = None,
                                 exit_ev_ebitda: float = None) -> dict:
    """Compute Gordon Growth TV, Exit-Multiple TV, and Blended TV.

    Returns dict with all three plus PVs and the chosen method.
    """
    if wacc <= terminal_g:
        gordon_tv = float("inf")
        pv_gordon = float("inf")
    else:
        terminal_fcf = last_fcf * (1 + terminal_g)
        gordon_tv = terminal_fcf / (wacc - terminal_g)
        pv_gordon = gordon_tv / (1 + wacc) ** horizon

    exit_tv = None
    pv_exit = None
    if last_ebitda_b is not None and exit_ev_ebitda is not None:
        exit_tv = exit_ev_ebitda * last_ebitda_b
        pv_exit = exit_tv / (1 + wacc) ** horizon

    blended_tv = None
    pv_blended = None
    if exit_tv is not None and gordon_tv != float("inf"):
        blended_tv = 0.5 * gordon_tv + 0.5 * exit_tv
        pv_blended = blended_tv / (1 + wacc) ** horizon

    return {
        "gordon": {"tv_b": gordon_tv, "pv_b": pv_gordon},
        "exit_multiple": {"tv_b": exit_tv, "pv_b": pv_exit, "multiple": exit_ev_ebitda},
        "blended": {"tv_b": blended_tv, "pv_b": pv_blended},
    }


# Sector WACC bands for adjudication crosscheck (Damodaran-aligned, rounded)
SECTOR_WACC_BANDS = {
    "semiconductors": (0.090, 0.120),
    "software": (0.090, 0.115),
    "saas": (0.100, 0.130),
    "ems": (0.080, 0.105),
    "ipp": (0.065, 0.090),  # independent power producer
    "utilities": (0.060, 0.080),
    "industrial": (0.080, 0.105),
    "miners": (0.100, 0.135),
    "photonics": (0.090, 0.115),
    "memory": (0.100, 0.130),
    "foundry": (0.090, 0.115),
    "power equipment": (0.080, 0.105),
}


def get_sector_wacc_band(sector_hint: str) -> tuple:
    """Lookup sector band by case-insensitive substring match. Defaults to (0.08, 0.12)."""
    if not sector_hint:
        return (0.08, 0.12)
    s = sector_hint.lower()
    for key, band in SECTOR_WACC_BANDS.items():
        if key in s:
            return band
    return (0.08, 0.12)


def compute_dcf_outputs(inputs: dict, net_debt_b: float, shares_b: float, build_trace: bool = False) -> dict:
    """Run DCF math given fully-specified inputs. Returns outputs dict.

    If build_trace=True, also returns a `calculation_trace` array of strings
    that document every step of the derivation. Set to False for sensitivity
    matrix evaluations (avoid trace pollution).
    """
    wacc = inputs["wacc"]["value"]
    terminal_g = inputs["terminal_growth"]["value"]
    fcf_proj = inputs["fcf_projections"]
    horizon = inputs.get("forecast_horizon_years", len(fcf_proj))

    if not fcf_proj:
        raise ValueError("fcf_projections is empty")
    if horizon != len(fcf_proj):
        # Pad if needed
        last = fcf_proj[-1]
        while len(fcf_proj) < horizon:
            fcf_proj.append({
                "year": last["year"] + 1,
                "fcf_b": last["fcf_b"] * 1.05,
                "rationale": "extrapolated at 5% from last projected year",
            })

    pv_explicit = 0.0
    for t, year_data in enumerate(fcf_proj, start=1):
        pv_explicit += year_data["fcf_b"] / (1 + wacc) ** t

    last_fcf = fcf_proj[-1]["fcf_b"]
    terminal_fcf = last_fcf * (1 + terminal_g)
    if wacc <= terminal_g:
        tv = float("inf")
        pv_terminal = float("inf")
    else:
        tv = terminal_fcf / (wacc - terminal_g)
        pv_terminal = tv / (1 + wacc) ** horizon

    implied_ev = pv_explicit + pv_terminal
    implied_equity = implied_ev - net_debt_b
    implied_px = implied_equity / shares_b if shares_b else 0.0

    result = {
        "pv_explicit_fcf_b": round(pv_explicit, 3),
        "terminal_value_b": round(tv, 3) if tv != float("inf") else None,
        "pv_terminal_b": round(pv_terminal, 3) if pv_terminal != float("inf") else None,
        "implied_ev_b": round(implied_ev, 3) if implied_ev != float("inf") else None,
        "implied_equity_b": round(implied_equity, 3) if implied_equity != float("inf") else None,
        "implied_px": round(implied_px, 2) if implied_px != float("inf") else None,
    }

    if build_trace:
        wacc_components = inputs["wacc"].get("components", {})
        rf = wacc_components.get("rf", 0)
        beta = wacc_components.get("beta", 0)
        erp = wacc_components.get("erp", 0)
        debt_w = wacc_components.get("debt_weight", 0)
        kd_at = wacc_components.get("cost_of_debt_after_tax", 0)
        ke = rf + beta * erp
        equity_w = 1 - debt_w

        # Full FCFF build per year (if richer inputs provided)
        fcff_build = []
        for year_data in fcf_proj:
            decomp = build_fcff_decomposition(year_data)
            decomp["year"] = year_data["year"]
            decomp["fcf_b"] = year_data["fcf_b"]
            fcff_build.append(decomp)

        per_year = []
        for t, year_data in enumerate(fcf_proj, start=1):
            df = 1 / (1 + wacc) ** t
            pv_y = year_data["fcf_b"] * df
            per_year.append({
                "year": year_data["year"],
                "fcf_b": round(year_data["fcf_b"], 3),
                "discount_factor": round(df, 4),
                "pv_b": round(pv_y, 3),
            })

        # Dual terminal value if exit multiple provided
        exit_ev_ebitda = inputs.get("terminal_value", {}).get("exit_ev_ebitda")
        last_year_data = fcf_proj[-1]
        last_ebitda_b = None
        if last_year_data.get("revenue_b") and last_year_data.get("ebit_margin") is not None:
            # Estimate terminal-year EBITDA = revenue × (ebit_margin + da_pct) if available
            da_pct = last_year_data.get("da_pct_of_revenue", 0.10)
            last_ebitda_b = last_year_data["revenue_b"] * (last_year_data["ebit_margin"] + da_pct)
        dual_tv = compute_dual_terminal_value(last_fcf, terminal_g, wacc, horizon,
                                              last_ebitda_b=last_ebitda_b,
                                              exit_ev_ebitda=exit_ev_ebitda)

        # WACC adjudication crosscheck
        sector_hint = inputs.get("sector_hint") or inputs.get("sector", "")
        band_lo, band_hi = get_sector_wacc_band(sector_hint)
        if wacc < band_lo:
            band_verdict = f"BELOW band ({band_lo*100:.1f}%-{band_hi*100:.1f}%)"
        elif wacc > band_hi:
            band_verdict = f"ABOVE band ({band_lo*100:.1f}%-{band_hi*100:.1f}%)"
        else:
            band_verdict = f"within band ({band_lo*100:.1f}%-{band_hi*100:.1f}%)"
        adjudication = {
            "sector": sector_hint or "(no sector hint)",
            "sector_band": [band_lo, band_hi],
            "formula_wacc": wacc,
            "verdict": band_verdict,
            "ke": ke,
            "notes": inputs.get("wacc", {}).get("adjudication_notes", ""),
        }

        trace = {
            "wacc": {
                "formula": "WACC = (1 - D/V) x Ke + (D/V) x Kd_after_tax;  Ke = Rf + Beta x ERP",
                "steps": [
                    {"label": "Cost of equity (Ke)", "expression": f"{rf:.4f} + {beta:.3f} x {erp:.4f}", "result": f"{ke:.4f} ({ke*100:.2f}%)"},
                    {"label": "Equity weight x Ke", "expression": f"{equity_w:.2f} x {ke:.4f}", "result": f"{equity_w*ke:.4f}"},
                    {"label": "Debt weight x after-tax Kd", "expression": f"{debt_w:.2f} x {kd_at:.4f}", "result": f"{debt_w*kd_at:.4f}"},
                    {"label": "WACC", "expression": f"{equity_w*ke:.4f} + {debt_w*kd_at:.4f}", "result": f"{wacc:.4f} ({wacc*100:.2f}%)"},
                ],
            },
            "explicit_fcf": {
                "formula": "PV explicit FCF = sum( FCF_t / (1+WACC)^t ) for t = 1..N",
                "per_year": per_year,
                "result": f"{pv_explicit:.3f}B",
            },
            "terminal_value": {
                "formula": "Terminal value = FCF_N x (1 + g_term) / (WACC - g_term);  PV TV = TV / (1+WACC)^N",
                "steps": [
                    {"label": "Last projected FCF", "expression": f"FCF_{horizon}", "result": f"{last_fcf:.3f}B"},
                    {"label": "Terminal FCF", "expression": f"{last_fcf:.3f} x (1 + {terminal_g:.4f})", "result": f"{terminal_fcf:.3f}B"},
                    {"label": "Terminal value", "expression": f"{terminal_fcf:.3f} / ({wacc:.4f} - {terminal_g:.4f})", "result": f"{tv:.3f}B" if tv != float("inf") else "infinite"},
                    {"label": "PV of terminal value", "expression": f"{tv:.3f} / (1+{wacc:.4f})^{horizon}" if tv != float("inf") else "n/a", "result": f"{pv_terminal:.3f}B" if pv_terminal != float("inf") else "n/a"},
                ],
            },
            "bridge_to_implied_px": {
                "formula": "Implied EV = PV explicit FCF + PV terminal;  Equity = EV - Net Debt;  Px = Equity / Shares",
                "steps": [
                    {"label": "Implied EV", "expression": f"{pv_explicit:.3f} + {pv_terminal:.3f}" if pv_terminal != float("inf") else "n/a", "result": f"{implied_ev:.3f}B" if implied_ev != float("inf") else "n/a"},
                    {"label": "Less: net debt", "expression": f"{implied_ev:.3f} - {net_debt_b:.3f}" if implied_ev != float("inf") else "n/a", "result": f"{implied_equity:.3f}B" if implied_equity != float("inf") else "n/a"},
                    {"label": "Implied price per share", "expression": f"{implied_equity:.3f}B / {shares_b:.3f}B shares" if implied_equity != float("inf") else "n/a", "result": f"${implied_px:.2f}" if implied_px != float("inf") else "n/a"},
                ],
            },
        }
        result["calculation_trace"] = trace
        result["fcff_build"] = fcff_build
        result["dual_terminal_value"] = {
            "gordon_tv_b": dual_tv["gordon"]["tv_b"] if dual_tv["gordon"]["tv_b"] != float("inf") else None,
            "gordon_pv_b": dual_tv["gordon"]["pv_b"] if dual_tv["gordon"]["pv_b"] != float("inf") else None,
            "exit_multiple_tv_b": dual_tv["exit_multiple"]["tv_b"],
            "exit_multiple_pv_b": dual_tv["exit_multiple"]["pv_b"],
            "exit_multiple_x": dual_tv["exit_multiple"]["multiple"],
            "blended_tv_b": dual_tv["blended"]["tv_b"],
            "blended_pv_b": dual_tv["blended"]["pv_b"],
            "terminal_ebitda_b": last_ebitda_b,
        }
        result["wacc_adjudication"] = adjudication

    return result


def compute_sensitivity(inputs: dict, net_debt_b: float, shares_b: float,
                        wacc_range: list, g_range: list) -> list:
    matrix = []
    for w in wacc_range:
        row = []
        for g in g_range:
            mod = json.loads(json.dumps(inputs))
            mod["wacc"]["value"] = w
            mod["terminal_growth"]["value"] = g
            try:
                out = compute_dcf_outputs(mod, net_debt_b, shares_b)
                row.append(out["implied_px"])
            except Exception:
                row.append(None)
        matrix.append(row)
    return matrix


def compute_multiple_outputs(cc_inputs: dict, net_debt_b: float, shares_b: float) -> dict:
    """Peer multiple cross-check. Supports EV/EBITDA, EV/Sales, P/E, P/B."""
    mult_type = cc_inputs.get("multiple_type", "EV/EBITDA")
    peer_med = cc_inputs["peer_median_multiple"]
    estimate = cc_inputs["fy_estimate"]

    if mult_type.startswith("EV/"):
        implied_ev = peer_med * estimate
        implied_equity = implied_ev - net_debt_b
        implied_px = implied_equity / shares_b if shares_b else 0.0
        return {
            "implied_ev_b": round(implied_ev, 3),
            "implied_equity_b": round(implied_equity, 3),
            "implied_px": round(implied_px, 2),
        }
    elif mult_type == "P/E":
        # estimate is EPS (USD or local currency, per share)
        implied_px = peer_med * estimate
        implied_equity = implied_px * shares_b
        return {
            "implied_equity_b": round(implied_equity, 3),
            "implied_px": round(implied_px, 2),
        }
    elif mult_type == "P/B":
        # estimate is BVPS
        implied_px = peer_med * estimate
        implied_equity = implied_px * shares_b
        return {
            "implied_equity_b": round(implied_equity, 3),
            "implied_px": round(implied_px, 2),
        }
    else:
        raise ValueError(f"Unsupported multiple_type: {mult_type}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True, help="Path to inputs JSON")
    parser.add_argument("--output", required=True, help="Path to output valuation JSON")
    args = parser.parse_args()

    with open(args.inputs, encoding="utf-8") as f:
        data = json.load(f)

    ticker = data["ticker"]
    currency = data.get("currency", "USD")
    current_price = data["current_price"]
    shares_b = data["shares_outstanding_b"]
    net_debt_b = data.get("net_debt_b", 0.0)

    primary = data["primary_method"]
    if primary["name"] != "DCF":
        sys.exit(f"ERROR: this script handles DCF only; got {primary['name']}. Use ddm_compute.py for DDM.")

    primary_inputs = primary["inputs"]

    # Base case math (with full calculation trace for the primary view)
    base_outputs = compute_dcf_outputs(primary_inputs, net_debt_b, shares_b, build_trace=True)

    # Auto-attach default reasoning per WACC component if missing
    wacc_components = primary_inputs.get("wacc", {}).get("components", {})
    wacc_components_reasoned = {}
    DEFAULT_COMPONENT_REASONING = {
        "rf": "Risk-free rate, typically the local 10Y government bond yield. US ~4.4%, EM countries higher.",
        "beta": "Equity beta vs local index. From yfinance unless sanitized to sector default.",
        "erp": "Equity risk premium. DM ~5.5%, EM Indonesia ~6.5%, EM India ~6.5%.",
        "debt_weight": "Debt portion of capital structure. Net debt / (net debt + market cap).",
        "cost_of_debt_after_tax": "Post-tax cost of debt. Yield on outstanding bonds x (1 - tax rate).",
    }
    for k, v in wacc_components.items():
        wacc_components_reasoned[k] = {
            "value": v,
            "reasoning": primary_inputs.get("wacc", {}).get("component_reasonings", {}).get(k, DEFAULT_COMPONENT_REASONING.get(k, "Input from inputs.json")),
        }
    primary_inputs.setdefault("wacc", {})["components_reasoned"] = wacc_components_reasoned

    # Sensitivity matrix
    wacc_base = primary_inputs["wacc"]["value"]
    g_base = primary_inputs["terminal_growth"]["value"]
    wacc_range = [round(wacc_base + delta, 4) for delta in [-0.010, -0.005, 0.0, 0.005, 0.010]]
    g_range = [round(g_base + delta, 4) for delta in [-0.010, -0.005, 0.0, 0.005, 0.010]]
    sens_matrix = compute_sensitivity(primary_inputs, net_debt_b, shares_b, wacc_range, g_range)

    # Scenarios — each has its own probability + reasoning
    scenarios_out = []
    base_px_for_blend = base_outputs["implied_px"]
    for scen in data.get("scenarios", []):
        scen_inputs = json.loads(json.dumps(primary_inputs))
        kc = scen.get("key_changes", {})
        if "wacc" in kc:
            scen_inputs["wacc"]["value"] = kc["wacc"]
        if "terminal_g" in kc:
            scen_inputs["terminal_growth"]["value"] = kc["terminal_g"]
        if "terminal_growth" in kc:
            scen_inputs["terminal_growth"]["value"] = kc["terminal_growth"]
        if "fcf_projections" in kc:
            scen_inputs["fcf_projections"] = kc["fcf_projections"]
        if "fcf_multiplier" in kc:
            for proj in scen_inputs["fcf_projections"]:
                proj["fcf_b"] = proj["fcf_b"] * kc["fcf_multiplier"]
        try:
            out = compute_dcf_outputs(scen_inputs, net_debt_b, shares_b)
            scen_px = out["implied_px"]
        except Exception:
            scen_px = None
        scenarios_out.append({
            "label": scen["label"],
            "key_changes": kc,
            "implied_px": scen_px,
            "probability": scen.get("probability"),
            "probability_reasoning": scen.get("probability_reasoning", ""),
            "reasoning": scen.get("reasoning", ""),
        })

    # Cross-check
    cross_check_out = None
    if "cross_check" in data and data["cross_check"]:
        cc = data["cross_check"]
        cc_outputs = compute_multiple_outputs(cc["inputs"], net_debt_b, shares_b)
        cross_check_out = {
            "name": cc["name"],
            "category": "relative",
            "reasoning": cc["reasoning"],
            "inputs": cc["inputs"],
            "outputs": cc_outputs,
        }

    # Blend — probabilities live on each scenario; cross_check weight at top level.
    # New protocol: sum of scenario.probability + blending_weights.cross_check should equal 1.0.
    weights = data.get("blending_weights") or {}
    cc_weight = weights.get("cross_check", 0)
    cc_px = cross_check_out["outputs"]["implied_px"] if cross_check_out else None

    # Sum of scenario contributions
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

    if cross_check_out and cc_weight > 0 and cc_px is not None:
        contrib = cc_weight * cc_px
        weighted_contribs.append({
            "component": cross_check_out["name"],
            "implied_px": cc_px,
            "weight": cc_weight,
            "contribution": round(contrib, 2),
            "reasoning": cross_check_out.get("reasoning", ""),
        })

    blended = sum(c["contribution"] for c in weighted_contribs)
    upside_pct = (blended - current_price) / current_price * 100 if current_price else None
    weight_total = scenario_weight_total + cc_weight

    valuation = {
        "schema_version": "1.0",
        "ticker": ticker,
        "currency": currency,
        "current_price": current_price,
        "shares_outstanding_b": shares_b,
        "net_debt_b": net_debt_b,
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "primary_method": {
            "name": "DCF",
            "category": "intrinsic",
            "reasoning": primary.get("reasoning", ""),
            "inputs": primary_inputs,
            "outputs": base_outputs,
            "sensitivity": {
                "rows_label": "WACC",
                "cols_label": "Terminal g",
                "row_values": wacc_range,
                "col_values": g_range,
                "implied_px_matrix": sens_matrix,
            },
        },
        "cross_check": cross_check_out,
        "scenarios": scenarios_out,
        "blended_target": round(blended, 2),
        "blending_logic": data.get("blending_logic", ""),
        "blending_weights": weights,
        "weights_reasoning": data.get("weights_reasoning", ""),
        "weighted_contributions": weighted_contribs,
        "weight_total_check": round(weight_total, 4),
        "upside_pct": round(upside_pct, 1) if upside_pct is not None else None,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(valuation, f, indent=2, ensure_ascii=False)

    print(f"[OK] wrote {out_path}")
    print(f"  Base DCF implied:    {base_outputs['implied_px']}")
    if cross_check_out:
        print(f"  Cross-check implied: {cross_check_out['outputs']['implied_px']}")
    for s in scenarios_out:
        print(f"  {s['label']:5s} implied:        {s['implied_px']}")
    print(f"  Blended target:      {round(blended, 2)} ({upside_pct:+.1f}%)")


if __name__ == "__main__":
    main()
