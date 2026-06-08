"""Sum-of-parts valuation engine. Each segment is valued with its own method.

Multi-segment conglomerates (AVGO with Semis + VMware Software, GEV with
Power + Wind + Electrification) trade at the wrong multiple if valued as a
single entity. SOTP values each segment via its appropriate method (DCF for
recurring-revenue businesses, multiple for cyclicals, etc.) and sums them.

Usage:
    python sotp_compute.py --inputs avgo_sotp_inputs.json --output avgo_valuation.json

The inputs JSON has a `segments` array. Each segment specifies its method
(`DCF` or `Multiple`) and its inputs. This script delegates to dcf_compute and
multiple-math helpers per segment, then aggregates.

Inputs JSON shape:
{
  "ticker": "AVGO",
  "currency": "USD",
  "current_price": ...,
  "shares_outstanding_b": ...,
  "net_debt_b": ...,
  "primary_method": {
    "name": "SOTP",
    "reasoning": "...",
    "inputs": {
      "segments": [
        {
          "name": "Semiconductor Solutions",
          "method": "DCF",
          "weight_pct": 60,
          "dcf_inputs": { ... same shape as DCF inputs ... }
        },
        {
          "name": "Infrastructure Software (VMware)",
          "method": "Multiple",
          "weight_pct": 40,
          "multiple_inputs": {
            "multiple_type": "EV/EBITDA",
            "multiple_value": 16,
            "fy_estimate": 12.0,
            "reasoning": "..."
          }
        }
      ]
    }
  },
  "scenarios": [...]
}
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dcf_compute import compute_dcf_outputs, compute_multiple_outputs


def value_segment(segment: dict, net_debt_b: float, shares_b: float) -> dict:
    """Run the chosen valuation for one segment. Returns per-segment outputs."""
    method = segment["method"]

    if method == "DCF":
        seg_inputs = segment["dcf_inputs"]
        # Each segment carries its own net debt allocation (default 0)
        seg_net_debt = segment.get("net_debt_allocation_b", 0.0)
        outputs = compute_dcf_outputs(seg_inputs, seg_net_debt, shares_b)
        return {
            "name": segment["name"],
            "method": "DCF",
            "weight_pct": segment.get("weight_pct"),
            "reasoning": segment.get("reasoning", ""),
            "inputs": seg_inputs,
            "outputs": outputs,
        }

    if method == "Multiple":
        mult_inputs = segment["multiple_inputs"]
        # For SOTP, we typically use enterprise multiples and sum EVs
        seg_inputs_for_mult = {
            "multiple_type": mult_inputs["multiple_type"],
            "peer_median_multiple": mult_inputs.get("multiple_value", mult_inputs.get("peer_median_multiple")),
            "fy_estimate": mult_inputs["fy_estimate"],
        }
        seg_net_debt = segment.get("net_debt_allocation_b", 0.0)
        outputs = compute_multiple_outputs(seg_inputs_for_mult, seg_net_debt, shares_b)
        return {
            "name": segment["name"],
            "method": "Multiple",
            "weight_pct": segment.get("weight_pct"),
            "reasoning": segment.get("reasoning", ""),
            "inputs": mult_inputs,
            "outputs": outputs,
        }

    raise ValueError(f"Unknown method: {method}")


def aggregate_segments(seg_outputs: list, parent_net_debt_b: float, shares_b: float) -> dict:
    """Sum segment EVs, subtract net debt, divide by shares."""
    total_ev = 0
    for s in seg_outputs:
        ev = s["outputs"].get("implied_ev_b")
        if ev is None:
            # If a segment is equity-priced (e.g., per-share P/E), convert to EV-equivalent
            implied_eq = s["outputs"].get("implied_equity_b")
            if implied_eq is not None:
                total_ev += implied_eq
                continue
            raise ValueError(f"Segment '{s['name']}' has no implied_ev_b or implied_equity_b")
        total_ev += ev
    implied_equity = total_ev - parent_net_debt_b
    implied_px = implied_equity / shares_b if shares_b else 0.0
    return {
        "sum_of_segment_ev_b": round(total_ev, 3),
        "net_debt_b": round(parent_net_debt_b, 3),
        "implied_equity_b": round(implied_equity, 3),
        "implied_px": round(implied_px, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.inputs, encoding="utf-8") as f:
        data = json.load(f)

    ticker = data["ticker"]
    currency = data.get("currency", "USD")
    current_price = data["current_price"]
    shares_b = data["shares_outstanding_b"]
    net_debt_b = data.get("net_debt_b", 0.0)

    primary = data["primary_method"]
    if primary["name"] != "SOTP":
        sys.exit(f"ERROR: SOTP script handles SOTP only; got {primary['name']}.")

    segments = primary["inputs"]["segments"]
    seg_outputs = [value_segment(s, net_debt_b, shares_b) for s in segments]
    aggregate = aggregate_segments(seg_outputs, net_debt_b, shares_b)

    # Scenarios: each scenario can override key_changes per segment
    scenarios_out = []
    for scen in data.get("scenarios", []):
        scen_segs = json.loads(json.dumps(segments))
        for seg_change in scen.get("segment_changes", []):
            target_name = seg_change["name"]
            for s in scen_segs:
                if s["name"] == target_name:
                    if "dcf_inputs" in s and "dcf_overrides" in seg_change:
                        for k, v in seg_change["dcf_overrides"].items():
                            if k == "wacc":
                                s["dcf_inputs"]["wacc"]["value"] = v
                            elif k == "terminal_g":
                                s["dcf_inputs"]["terminal_growth"]["value"] = v
                            elif k == "fcf_multiplier":
                                for p in s["dcf_inputs"]["fcf_projections"]:
                                    p["fcf_b"] *= v
                    if "multiple_inputs" in s and "multiple_overrides" in seg_change:
                        for k, v in seg_change["multiple_overrides"].items():
                            s["multiple_inputs"][k] = v
        scen_seg_outputs = [value_segment(s, net_debt_b, shares_b) for s in scen_segs]
        scen_agg = aggregate_segments(scen_seg_outputs, net_debt_b, shares_b)
        scenarios_out.append({
            "label": scen["label"],
            "key_changes": scen.get("segment_changes", []),
            "implied_px": scen_agg["implied_px"],
            "reasoning": scen.get("reasoning", ""),
        })

    blended = aggregate["implied_px"]
    upside_pct = (blended - current_price) / current_price * 100 if current_price else None

    valuation = {
        "schema_version": "1.0",
        "ticker": ticker,
        "currency": currency,
        "current_price": current_price,
        "shares_outstanding_b": shares_b,
        "net_debt_b": net_debt_b,
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "primary_method": {
            "name": "SOTP",
            "category": "intrinsic",
            "reasoning": primary.get("reasoning", ""),
            "inputs": {"segments": segments},
            "outputs": {
                "segments": seg_outputs,
                "aggregate": aggregate,
                "implied_px": aggregate["implied_px"],
            },
        },
        "cross_check": data.get("cross_check"),
        "scenarios": scenarios_out,
        "blended_target": round(blended, 2),
        "blending_logic": data.get("blending_logic", "Pure SOTP — sum of segment EVs less parent net debt"),
        "upside_pct": round(upside_pct, 1) if upside_pct is not None else None,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(valuation, f, indent=2, ensure_ascii=False)

    print(f"[OK] wrote {out_path}")
    print(f"  Segments:")
    for s in seg_outputs:
        px = s["outputs"].get("implied_px", "n/a")
        ev = s["outputs"].get("implied_ev_b", "n/a")
        print(f"    {s['name']:40s}  EV={ev}  px={px}  ({s['method']})")
    print(f"  Aggregate implied px: {aggregate['implied_px']}  ({upside_pct:+.1f}%)")


if __name__ == "__main__":
    main()
