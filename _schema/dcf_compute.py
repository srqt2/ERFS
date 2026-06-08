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


def compute_dcf_outputs(inputs: dict, net_debt_b: float, shares_b: float) -> dict:
    """Run DCF math given fully-specified inputs. Returns outputs dict."""
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

    return {
        "pv_explicit_fcf_b": round(pv_explicit, 3),
        "terminal_value_b": round(tv, 3) if tv != float("inf") else None,
        "pv_terminal_b": round(pv_terminal, 3) if pv_terminal != float("inf") else None,
        "implied_ev_b": round(implied_ev, 3) if implied_ev != float("inf") else None,
        "implied_equity_b": round(implied_equity, 3) if implied_equity != float("inf") else None,
        "implied_px": round(implied_px, 2) if implied_px != float("inf") else None,
    }


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

    # Base case math
    base_outputs = compute_dcf_outputs(primary_inputs, net_debt_b, shares_b)

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
