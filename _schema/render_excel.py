"""Render an Excel workbook from the narrative + valuation JSONs.

Same JSON in -> same xlsx out. The agent does not touch Excel layout.

Usage:
    python render_excel.py --ticker AVGO --category AI
       reads:  output/AVGO/AVGO.json + output/AVGO/AVGO_valuation.json
       writes: output/AVGO/AVGO.xlsx

Tabs (locked order):
    1. Cover           ticker, recommendation, target, top-line thesis
    2. Assumptions     every input cell with reasoning column. EDITABLE.
    3. Calculation     DCF / DDM / SOTP math with formulas
    4. Sensitivity     Ke or WACC x g_terminal grid
    5. Scenarios       Bull / Base / Bear
    6. Peers           peer table with reason for inclusion
    7. Reasoning Log   prose explaining each decision
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("ERROR: openpyxl not installed. Run: pip install openpyxl")


# Style primitives
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name="Calibri", size=14, bold=True)
SUBTITLE_FONT = Font(name="Calibri", size=10, italic=True, color="6B7280")
BODY_FONT = Font(name="Calibri", size=10)
BODY_BOLD = Font(name="Calibri", size=10, bold=True)
MUTED_FONT = Font(name="Calibri", size=9, color="6B7280")

HEADER_FILL = PatternFill("solid", fgColor="111827")
SECTION_FILL = PatternFill("solid", fgColor="F3F4F6")
HIGHLIGHT_FILL = PatternFill("solid", fgColor="DBEAFE")
GOOD_FILL = PatternFill("solid", fgColor="DCFCE7")
BAD_FILL = PatternFill("solid", fgColor="FEE2E2")
NEUTRAL_FILL = PatternFill("solid", fgColor="FEF3C7")

THIN = Side(style="thin", color="E5E7EB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def fmt_currency(v, currency):
    if v is None:
        return "n/a"
    if currency == "IDR":
        return f"IDR {v:,.0f}"
    return f"${v:,.2f}" if abs(v) < 100 else f"${v:,.0f}"


def autosize(ws, max_width=80):
    for col_letter, max_len in {}.items():
        ws.column_dimensions[col_letter].width = min(max_len + 2, max_width)
    # Compute from cell contents
    for col_idx in range(1, ws.max_column + 1):
        letter = get_column_letter(col_idx)
        max_len = 8
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value is not None:
                    cell_str = str(cell.value)
                    if len(cell_str) > max_len:
                        max_len = len(cell_str)
        ws.column_dimensions[letter].width = min(max_len + 2, max_width)


# ============================================================
# TAB BUILDERS
# ============================================================

def build_cover(wb, narrative, valuation):
    ws = wb.create_sheet("Cover")
    rec = narrative.get("recommendation", {})
    currency = valuation.get("currency", "USD")

    rows = [
        ("Ticker", narrative["ticker"]),
        ("Company", narrative["name"]),
        ("Listings", narrative.get("listings", "")),
        ("Sector", narrative.get("sector", "")),
        ("Date", narrative.get("date", "")),
        ("", ""),
        ("Recommendation", rec.get("action", "")),
        ("Current price", fmt_currency(rec.get("current_price"), currency)),
        ("12m target", fmt_currency(rec.get("target_12m"), currency)),
        ("Upside %", f"{rec.get('upside_pct', 0):+.1f}%"),
        ("Next earnings", rec.get("next_earnings", "")),
        ("", ""),
        ("Valuation method", valuation.get("primary_method", {}).get("name", "")),
        ("Blended target (math)", fmt_currency(valuation.get("blended_target"), currency)),
        ("Cost of capital", f"{valuation.get('primary_method', {}).get('outputs', {}).get('cost_of_equity_pct', 0):.2f}%"
                            if valuation.get("primary_method", {}).get("name") == "DDM"
                            else f"{valuation.get('primary_method', {}).get('inputs', {}).get('wacc', {}).get('value', 0)*100:.2f}%"
                            if valuation.get("primary_method", {}).get("name") == "DCF" else ""),
        ("", ""),
    ]

    ws["A1"] = f"{narrative['ticker']} — {narrative['name']}"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:C1")

    ws["A2"] = "Equity Research"
    ws["A2"].font = SUBTITLE_FONT
    ws.merge_cells("A2:C2")

    for i, (label, value) in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=label).font = BODY_BOLD
        ws.cell(row=i, column=2, value=value).font = BODY_FONT

    # Thesis (3 bullets)
    base_row = 4 + len(rows) + 1
    ws.cell(row=base_row, column=1, value="Thesis").font = BODY_BOLD
    ws.cell(row=base_row, column=1).fill = SECTION_FILL
    for j, t in enumerate(narrative.get("thesis", [])):
        ws.cell(row=base_row + 1 + j, column=1, value=f"{j+1}.")
        ws.cell(row=base_row + 1 + j, column=2, value=t).alignment = Alignment(wrap_text=True, vertical="top")

    autosize(ws)
    ws.column_dimensions["B"].width = 80


def build_assumptions(wb, valuation):
    ws = wb.create_sheet("Assumptions")
    primary = valuation.get("primary_method", {})
    method = primary.get("name", "")

    ws["A1"] = f"Assumptions — {method}"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "EDIT cells in column B to flex the model. Reasoning is in column C."
    ws["A2"].font = SUBTITLE_FONT

    headers = ["Input", "Value", "Reasoning"]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL

    inputs = primary.get("inputs", {}) or {}
    row = 5

    def write_input(label, value, reasoning=""):
        nonlocal row
        ws.cell(row=row, column=1, value=label).font = BODY_BOLD
        ws.cell(row=row, column=2, value=value).font = BODY_FONT
        ws.cell(row=row, column=3, value=reasoning).font = MUTED_FONT
        ws.cell(row=row, column=3).alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    if method == "DCF":
        wacc = inputs.get("wacc", {})
        write_input("WACC", wacc.get("value"), wacc.get("reasoning", ""))
        # Prefer per-component reasoning if available
        components_reasoned = wacc.get("components_reasoned") or {}
        if components_reasoned:
            for k, comp in components_reasoned.items():
                v = comp.get("value") if isinstance(comp, dict) else comp
                r = comp.get("reasoning", "") if isinstance(comp, dict) else ""
                write_input(f"  - {k}", v, r)
        else:
            for k, v in (wacc.get("components") or {}).items():
                write_input(f"  - {k}", v, "")
        tg = inputs.get("terminal_growth", {})
        write_input("Terminal growth", tg.get("value"), tg.get("reasoning", ""))
        write_input("Forecast horizon (yrs)", inputs.get("forecast_horizon_years"), "")
        row += 1
        ws.cell(row=row, column=1, value="FCF projections").font = BODY_BOLD
        ws.cell(row=row, column=1).fill = SECTION_FILL
        row += 1
        ws.cell(row=row, column=1, value="Year").font = BODY_BOLD
        ws.cell(row=row, column=2, value="FCF (bn)").font = BODY_BOLD
        ws.cell(row=row, column=3, value="Rationale").font = BODY_BOLD
        row += 1
        for proj in inputs.get("fcf_projections", []):
            ws.cell(row=row, column=1, value=proj.get("year"))
            ws.cell(row=row, column=2, value=proj.get("fcf_b"))
            ws.cell(row=row, column=3, value=proj.get("rationale", "")).alignment = Alignment(wrap_text=True, vertical="top")
            row += 1

    elif method == "DDM":
        # Prefer the explicit *_reasoning fields produced by ddm_compute.
        for label, key, reasoning_field in [
            ("Risk-free rate (rf)", "rf", "rf_reasoning"),
            ("Equity risk premium (ERP)", "erp", "erp_reasoning"),
            ("Beta", "beta", "beta_reasoning"),
            ("Sustainable D0", "d0", "d0_reasoning"),
            ("Book value / share", "book_value_per_share", "book_value_reasoning"),
            ("ROE", "roe", "roe_reasoning"),
            ("g_high (years 1-5)", "g_high", "g_high_reasoning"),
            ("g_terminal", "g_terminal", "g_terminal_reasoning"),
            ("g_book", "g_book", "g_book_reasoning"),
            ("Years high growth", "years_high", None),
            ("Years declining", "years_decline", None),
            ("Excess returns horizon", "horizon_excess_returns", None),
            ("Max sustainable payout", "max_payout_cap", None),
        ]:
            reasoning_text = inputs.get(reasoning_field, "") if reasoning_field else ""
            write_input(label, inputs.get(key), reasoning_text)

    elif method == "SOTP":
        ws.cell(row=row, column=1, value="Segments").font = BODY_BOLD
        ws.cell(row=row, column=1).fill = SECTION_FILL
        row += 1
        for seg in inputs.get("segments", []) or primary.get("outputs", {}).get("segments", []):
            ws.cell(row=row, column=1, value=seg["name"]).font = BODY_BOLD
            ws.cell(row=row, column=2, value=seg.get("method", "")).font = BODY_FONT
            ws.cell(row=row, column=3, value=seg.get("reasoning", "")).font = MUTED_FONT
            ws.cell(row=row, column=3).alignment = Alignment(wrap_text=True, vertical="top")
            row += 1

    autosize(ws)
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 80


def build_calculation(wb, valuation):
    ws = wb.create_sheet("Calculation")
    primary = valuation.get("primary_method", {})
    method = primary.get("name", "")
    currency = valuation.get("currency", "USD")

    ws["A1"] = f"{method} calculation"
    ws["A1"].font = TITLE_FONT

    if method == "DCF":
        inputs = primary.get("inputs", {})
        outputs = primary.get("outputs", {})
        ws["A3"] = "Year"
        ws["A3"].font = HEADER_FONT
        ws["A3"].fill = HEADER_FILL
        ws["B3"] = "FCF (bn)"
        ws["B3"].font = HEADER_FONT
        ws["B3"].fill = HEADER_FILL
        ws["C3"] = "Discount factor"
        ws["C3"].font = HEADER_FONT
        ws["C3"].fill = HEADER_FILL
        ws["D3"] = "PV (bn)"
        ws["D3"].font = HEADER_FONT
        ws["D3"].fill = HEADER_FILL
        wacc = inputs.get("wacc", {}).get("value", 0.10)
        row = 4
        for t, proj in enumerate(inputs.get("fcf_projections", []), start=1):
            ws.cell(row=row, column=1, value=proj.get("year"))
            ws.cell(row=row, column=2, value=proj.get("fcf_b"))
            ws.cell(row=row, column=3, value=round(1 / (1 + wacc) ** t, 4))
            ws.cell(row=row, column=4, value=round(proj.get("fcf_b", 0) / (1 + wacc) ** t, 3))
            row += 1
        ws.cell(row=row + 1, column=1, value="PV of explicit FCF").font = BODY_BOLD
        ws.cell(row=row + 1, column=4, value=outputs.get("pv_explicit_fcf_b"))
        ws.cell(row=row + 2, column=1, value="Terminal value").font = BODY_BOLD
        ws.cell(row=row + 2, column=4, value=outputs.get("terminal_value_b"))
        ws.cell(row=row + 3, column=1, value="PV of terminal value").font = BODY_BOLD
        ws.cell(row=row + 3, column=4, value=outputs.get("pv_terminal_b"))
        ws.cell(row=row + 5, column=1, value="Implied EV").font = BODY_BOLD
        ws.cell(row=row + 5, column=4, value=outputs.get("implied_ev_b")).fill = HIGHLIGHT_FILL
        ws.cell(row=row + 6, column=1, value="(less) Net debt").font = BODY_BOLD
        ws.cell(row=row + 6, column=4, value=valuation.get("net_debt_b"))
        ws.cell(row=row + 7, column=1, value="Implied equity value").font = BODY_BOLD
        ws.cell(row=row + 7, column=4, value=outputs.get("implied_equity_b"))
        ws.cell(row=row + 8, column=1, value="Shares outstanding (bn)").font = BODY_BOLD
        ws.cell(row=row + 8, column=4, value=valuation.get("shares_outstanding_b"))
        ws.cell(row=row + 10, column=1, value="Implied price per share").font = BODY_BOLD
        cell = ws.cell(row=row + 10, column=4, value=outputs.get("implied_px"))
        cell.fill = HIGHLIGHT_FILL
        cell.font = Font(name="Calibri", size=12, bold=True)

    elif method == "DDM":
        outputs = primary.get("outputs", {})
        inputs = primary.get("inputs", {})
        ddm = outputs.get("ddm", {})
        ws["A3"] = "Year"
        ws["B3"] = "Dividend"
        ws["A3"].font = HEADER_FONT; ws["A3"].fill = HEADER_FILL
        ws["B3"].font = HEADER_FONT; ws["B3"].fill = HEADER_FILL
        for i, d in enumerate(ddm.get("explicit_dividends", []), start=1):
            ws.cell(row=3 + i, column=1, value=f"Year {i}")
            ws.cell(row=3 + i, column=2, value=round(d, 2))
        row = 3 + len(ddm.get("explicit_dividends", [])) + 2
        ws.cell(row=row, column=1, value="PV of explicit dividends").font = BODY_BOLD
        ws.cell(row=row, column=2, value=ddm.get("pv_explicit"))
        ws.cell(row=row + 1, column=1, value="PV of terminal value").font = BODY_BOLD
        ws.cell(row=row + 1, column=2, value=ddm.get("pv_terminal"))
        ws.cell(row=row + 2, column=1, value="DDM implied value").font = BODY_BOLD
        ws.cell(row=row + 2, column=2, value=ddm.get("implied_value")).fill = HIGHLIGHT_FILL

        er = outputs.get("excess_returns", {})
        ws.cell(row=row + 4, column=1, value="Excess Returns Model").font = BODY_BOLD
        ws.cell(row=row + 4, column=1).fill = SECTION_FILL
        ws.cell(row=row + 5, column=1, value="Book value 0")
        ws.cell(row=row + 5, column=2, value=er.get("book_value"))
        ws.cell(row=row + 6, column=1, value="PV of excess returns")
        ws.cell(row=row + 6, column=2, value=er.get("pv_excess"))
        ws.cell(row=row + 7, column=1, value="ER implied value").font = BODY_BOLD
        ws.cell(row=row + 7, column=2, value=er.get("implied_value")).fill = HIGHLIGHT_FILL

        jpb = outputs.get("justified_pb", {})
        ws.cell(row=row + 9, column=1, value="Justified P/B").font = BODY_BOLD
        ws.cell(row=row + 9, column=1).fill = SECTION_FILL
        ws.cell(row=row + 10, column=1, value="Ratio")
        ws.cell(row=row + 10, column=2, value=jpb.get("ratio"))
        ws.cell(row=row + 11, column=1, value="Implied value").font = BODY_BOLD
        ws.cell(row=row + 11, column=2, value=jpb.get("implied_value")).fill = HIGHLIGHT_FILL

        ws.cell(row=row + 13, column=1, value="Blended implied price").font = BODY_BOLD
        cell = ws.cell(row=row + 13, column=2, value=outputs.get("implied_px"))
        cell.fill = HIGHLIGHT_FILL
        cell.font = Font(name="Calibri", size=12, bold=True)

    elif method == "SOTP":
        outputs = primary.get("outputs", {})
        ws["A3"] = "Segment"; ws["A3"].font = HEADER_FONT; ws["A3"].fill = HEADER_FILL
        ws["B3"] = "Method"; ws["B3"].font = HEADER_FONT; ws["B3"].fill = HEADER_FILL
        ws["C3"] = "EV (bn)"; ws["C3"].font = HEADER_FONT; ws["C3"].fill = HEADER_FILL
        ws["D3"] = "Implied px contribution"; ws["D3"].font = HEADER_FONT; ws["D3"].fill = HEADER_FILL
        row = 4
        for seg in outputs.get("segments", []):
            ws.cell(row=row, column=1, value=seg["name"])
            ws.cell(row=row, column=2, value=seg["method"])
            ws.cell(row=row, column=3, value=seg["outputs"].get("implied_ev_b"))
            ws.cell(row=row, column=4, value=seg["outputs"].get("implied_px"))
            row += 1
        agg = outputs.get("aggregate", {})
        ws.cell(row=row + 1, column=1, value="Sum of segment EV").font = BODY_BOLD
        ws.cell(row=row + 1, column=3, value=agg.get("sum_of_segment_ev_b"))
        ws.cell(row=row + 2, column=1, value="(less) Parent net debt").font = BODY_BOLD
        ws.cell(row=row + 2, column=3, value=agg.get("net_debt_b"))
        ws.cell(row=row + 3, column=1, value="Implied equity").font = BODY_BOLD
        ws.cell(row=row + 3, column=3, value=agg.get("implied_equity_b"))
        ws.cell(row=row + 5, column=1, value="Implied px / share").font = BODY_BOLD
        cell = ws.cell(row=row + 5, column=3, value=agg.get("implied_px"))
        cell.fill = HIGHLIGHT_FILL
        cell.font = Font(name="Calibri", size=12, bold=True)

    autosize(ws)


def build_sensitivity(wb, valuation):
    ws = wb.create_sheet("Sensitivity")
    primary = valuation.get("primary_method", {})
    sens = primary.get("sensitivity")
    if not sens:
        ws["A1"] = "No sensitivity matrix available"
        return
    ws["A1"] = f"Sensitivity: {sens.get('rows_label', 'rows')} x {sens.get('cols_label', 'cols')}"
    ws["A1"].font = TITLE_FONT
    rows_label = sens.get("rows_label", "")
    cols_label = sens.get("cols_label", "")
    row_vals = sens.get("row_values", [])
    col_vals = sens.get("col_values", [])
    matrix = sens.get("implied_px_matrix", [])

    ws.cell(row=3, column=1, value=f"{rows_label} \\ {cols_label}").font = HEADER_FONT
    ws.cell(row=3, column=1).fill = HEADER_FILL
    for j, c in enumerate(col_vals):
        cell = ws.cell(row=3, column=2 + j, value=f"{c*100:.2f}%")
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
    for i, r in enumerate(row_vals):
        cell = ws.cell(row=4 + i, column=1, value=f"{r*100:.2f}%")
        cell.font = BODY_BOLD
        cell.fill = SECTION_FILL
        for j, _ in enumerate(col_vals):
            v = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else None
            ws.cell(row=4 + i, column=2 + j, value=v)
    autosize(ws)


def build_scenarios(wb, valuation):
    ws = wb.create_sheet("Scenarios")
    ws["A1"] = "Scenarios + probability weights"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Each scenario has its own probability. Final target = sum of (prob x implied px) + cross-check weight x cross-check implied px."
    ws["A2"].font = SUBTITLE_FONT
    ws.merge_cells("A2:F2")

    headers = ["Scenario", "Implied px", "Probability", "Weighted contribution", "Probability reasoning", "Scenario narrative"]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL

    row = 5
    scenarios = valuation.get("scenarios", [])
    currency = valuation.get("currency", "USD")
    for scen in scenarios:
        ws.cell(row=row, column=1, value=scen["label"]).font = BODY_BOLD
        ws.cell(row=row, column=2, value=scen.get("implied_px"))
        prob = scen.get("probability")
        ws.cell(row=row, column=3, value=prob)
        if prob is not None and scen.get("implied_px") is not None:
            ws.cell(row=row, column=4, value=round(prob * scen["implied_px"], 2))
        ws.cell(row=row, column=5, value=scen.get("probability_reasoning", "")).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=row, column=6, value=scen.get("reasoning", "")).alignment = Alignment(wrap_text=True, vertical="top")
        # tone fill
        if scen["label"].lower() == "bull":
            ws.cell(row=row, column=1).fill = GOOD_FILL
        elif scen["label"].lower() == "bear":
            ws.cell(row=row, column=1).fill = BAD_FILL
        else:
            ws.cell(row=row, column=1).fill = NEUTRAL_FILL
        row += 1

    # Cross-check row (if present)
    cc = valuation.get("cross_check")
    cc_weight = (valuation.get("blending_weights") or {}).get("cross_check", 0)
    if cc and cc_weight > 0:
        ws.cell(row=row, column=1, value=cc.get("name", "Cross-check")).font = BODY_BOLD
        ws.cell(row=row, column=1).fill = HIGHLIGHT_FILL
        ws.cell(row=row, column=2, value=cc.get("outputs", {}).get("implied_px"))
        ws.cell(row=row, column=3, value=cc_weight)
        cc_px = cc.get("outputs", {}).get("implied_px")
        if cc_px is not None:
            ws.cell(row=row, column=4, value=round(cc_weight * cc_px, 2))
        ws.cell(row=row, column=5, value=cc.get("reasoning", "")).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=row, column=6, value="Peer multiple cross-check (independent of DCF/DDM)").alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    # Totals row
    row += 1
    ws.cell(row=row, column=1, value="Weight total").font = BODY_BOLD
    weight_total = valuation.get("weight_total_check")
    if weight_total is None:
        weight_total = sum((s.get("probability") or 0) for s in scenarios) + cc_weight
    ws.cell(row=row, column=3, value=weight_total)
    if abs(weight_total - 1.0) > 0.001:
        ws.cell(row=row, column=3).fill = BAD_FILL
        ws.cell(row=row, column=5, value="WARNING: weights do not sum to 1.0").font = Font(name="Calibri", size=10, bold=True, color="991B1B")

    row += 1
    ws.cell(row=row, column=1, value="Blended target").font = Font(name="Calibri", size=12, bold=True)
    ws.cell(row=row, column=4, value=valuation.get("blended_target")).font = Font(name="Calibri", size=12, bold=True)
    ws.cell(row=row, column=4).fill = HIGHLIGHT_FILL

    row += 1
    cp = valuation.get("current_price")
    if cp is not None:
        ws.cell(row=row, column=1, value="Current price").font = BODY_BOLD
        ws.cell(row=row, column=4, value=cp)
        upside = valuation.get("upside_pct")
        if upside is not None:
            ws.cell(row=row + 1, column=1, value="Upside / (downside) %").font = BODY_BOLD
            up_cell = ws.cell(row=row + 1, column=4, value=upside / 100)
            up_cell.number_format = "0.0%"
            if upside >= 0:
                up_cell.fill = GOOD_FILL
            else:
                up_cell.fill = BAD_FILL

    # Reasoning panel
    row += 4
    ws.cell(row=row, column=1, value="Why these weights?").font = BODY_BOLD
    ws.cell(row=row, column=1).fill = SECTION_FILL
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1
    weights_reasoning = valuation.get("weights_reasoning") or valuation.get("blending_logic", "")
    if weights_reasoning:
        ws.cell(row=row, column=1, value=weights_reasoning).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)

    autosize(ws)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 13
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 55
    ws.column_dimensions["F"].width = 55

    # Format probability + weighted columns as numbers / percentages
    for r in range(5, row + 1):
        prob_cell = ws.cell(row=r, column=3)
        if isinstance(prob_cell.value, (int, float)) and 0 <= prob_cell.value <= 1:
            prob_cell.number_format = "0.0%"


def build_peers(wb, narrative):
    ws = wb.create_sheet("Peers")
    ws["A1"] = "Peer comparison"
    ws["A1"].font = TITLE_FONT

    headers = ["Ticker", "NTM P/E", "EV/EBITDA", "Rev growth (TTM)", "YTD", "1Y", ""]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=c, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL

    for i, p in enumerate(narrative.get("peers", []), start=4):
        ws.cell(row=i, column=1, value=p["ticker"]).font = BODY_BOLD
        if p.get("highlight"):
            for c in range(1, 8):
                ws.cell(row=i, column=c).fill = HIGHLIGHT_FILL
        ws.cell(row=i, column=2, value=p.get("pe_ntm"))
        ws.cell(row=i, column=3, value=p.get("ev_ebitda"))
        ws.cell(row=i, column=4, value=p.get("rev_growth_ttm"))
        ws.cell(row=i, column=5, value=p.get("ytd"))
        ws.cell(row=i, column=6, value=p.get("y1"))

    pr = narrative.get("peers_read", "")
    if pr:
        row = 4 + len(narrative.get("peers", [])) + 2
        ws.cell(row=row, column=1, value="Read:").font = BODY_BOLD
        ws.cell(row=row, column=2, value=pr).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
    autosize(ws)


def build_formula_trace(wb, valuation):
    """A tab dedicated to the step-by-step formula derivation of every derived number.

    Trace lives in valuation.primary_method.calculation_trace OR
    valuation.primary_method.outputs.calculation_trace.
    """
    primary = valuation.get("primary_method", {})
    trace = primary.get("calculation_trace") or primary.get("outputs", {}).get("calculation_trace")
    if not trace:
        return
    ws = wb.create_sheet("Formula trace")
    ws["A1"] = "Formula trace"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Step-by-step derivation. Same inputs always produce these numbers."
    ws["A2"].font = SUBTITLE_FONT

    row = 4
    SECTION_ORDER = [
        ("wacc", "WACC / Cost of equity"),
        ("ke", "Cost of equity (Ke)"),
        ("explicit_fcf", "PV of explicit FCF"),
        ("ddm", "DDM value"),
        ("excess_returns", "Excess Returns Model"),
        ("justified_pb", "Justified P/B"),
        ("terminal_value", "Terminal value"),
        ("bridge_to_implied_px", "Bridge to implied price per share"),
        ("blended", "Blended target"),
    ]
    for key, label in SECTION_ORDER:
        calc = trace.get(key)
        if not calc:
            continue
        # Section title
        title_cell = ws.cell(row=row, column=1, value=label)
        title_cell.font = BODY_BOLD
        title_cell.fill = SECTION_FILL
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        row += 1
        formula_cell = ws.cell(row=row, column=1, value=f"Formula: {calc.get('formula', '')}")
        formula_cell.font = Font(name="Calibri", size=10, italic=True, color="6B7280")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        row += 1
        # Header row
        ws.cell(row=row, column=1, value="Step").font = HEADER_FONT
        ws.cell(row=row, column=1).fill = HEADER_FILL
        ws.cell(row=row, column=2, value="Expression").font = HEADER_FONT
        ws.cell(row=row, column=2).fill = HEADER_FILL
        ws.cell(row=row, column=3, value="Result").font = HEADER_FONT
        ws.cell(row=row, column=3).fill = HEADER_FILL
        row += 1
        steps = calc.get("steps") or []
        for s in steps:
            ws.cell(row=row, column=1, value=s.get("label", "")).font = BODY_FONT
            ws.cell(row=row, column=2, value=s.get("expression", "")).font = Font(name="Consolas", size=10)
            result_cell = ws.cell(row=row, column=3, value=s.get("result", ""))
            result_cell.font = BODY_BOLD
            row += 1
        # Per-year FCF table for explicit_fcf
        per_year = calc.get("per_year")
        if per_year:
            ws.cell(row=row, column=1, value="Year").font = HEADER_FONT
            ws.cell(row=row, column=1).fill = HEADER_FILL
            ws.cell(row=row, column=2, value="FCF (bn)").font = HEADER_FONT
            ws.cell(row=row, column=2).fill = HEADER_FILL
            ws.cell(row=row, column=3, value="Discount factor").font = HEADER_FONT
            ws.cell(row=row, column=3).fill = HEADER_FILL
            ws.cell(row=row, column=4, value="PV (bn)").font = HEADER_FONT
            ws.cell(row=row, column=4).fill = HEADER_FILL
            row += 1
            for y in per_year:
                ws.cell(row=row, column=1, value=y.get("year"))
                ws.cell(row=row, column=2, value=y.get("fcf_b"))
                ws.cell(row=row, column=3, value=y.get("discount_factor"))
                ws.cell(row=row, column=4, value=y.get("pv_b"))
                row += 1
            ws.cell(row=row, column=1, value="Sum (PV explicit FCF)").font = BODY_BOLD
            ws.cell(row=row, column=4, value=calc.get("result")).font = BODY_BOLD
            ws.cell(row=row, column=4).fill = HIGHLIGHT_FILL
            row += 1
        result_text = calc.get("result")
        if result_text and not per_year:
            ws.cell(row=row, column=1, value="Result").font = BODY_BOLD
            ws.cell(row=row, column=3, value=result_text).font = BODY_BOLD
            ws.cell(row=row, column=3).fill = HIGHLIGHT_FILL
            row += 1
        row += 1  # spacer
    autosize(ws)
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 45
    ws.column_dimensions["C"].width = 28
    ws.column_dimensions["D"].width = 14


def build_reasoning_log(wb, narrative, valuation):
    ws = wb.create_sheet("Reasoning Log")
    ws["A1"] = "Reasoning log"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Why each method / assumption / peer was chosen."
    ws["A2"].font = SUBTITLE_FONT

    row = 4
    primary = valuation.get("primary_method", {})

    def add(label, value):
        nonlocal row
        ws.cell(row=row, column=1, value=label).font = BODY_BOLD
        ws.cell(row=row, column=1).fill = SECTION_FILL
        ws.cell(row=row, column=2, value=value).alignment = Alignment(wrap_text=True, vertical="top")
        row += 2

    add("Valuation method", f"{primary.get('name')} — {primary.get('reasoning', '')}")

    method = primary.get("name")
    inputs = primary.get("inputs", {})
    if method == "DCF":
        wacc = inputs.get("wacc", {})
        add("WACC reasoning", wacc.get("reasoning", ""))
        add("Terminal growth reasoning", inputs.get("terminal_growth", {}).get("reasoning", ""))
    elif method == "DDM":
        add("DDM reasoning", inputs.get("reasoning", ""))
    elif method == "SOTP":
        for seg in primary.get("outputs", {}).get("segments", []):
            add(f"{seg['name']} ({seg['method']})", seg.get("reasoning", ""))

    cc = valuation.get("cross_check")
    if cc:
        add("Cross-check", f"{cc.get('name')} — {cc.get('reasoning', '')}")

    add("Blending logic", valuation.get("blending_logic", ""))

    if narrative.get("data_gaps"):
        add("Data gaps", " | ".join(narrative["data_gaps"]))

    autosize(ws)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--category", required=True, help="AI or IDX")
    parser.add_argument("--narrative", help="Path to narrative JSON (default: output/<TICKER>/<TICKER>.json)")
    parser.add_argument("--valuation", help="Path to valuation JSON (default: output/<TICKER>/<TICKER>_valuation.json)")
    parser.add_argument("--output", help="Path to output xlsx (default: output/<TICKER>/<TICKER>.xlsx)")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent / "output" / args.ticker
    narrative_path = Path(args.narrative) if args.narrative else (base_dir / f"{args.ticker}.json")
    valuation_path = Path(args.valuation) if args.valuation else (base_dir / f"{args.ticker}_valuation.json")
    output_path = Path(args.output) if args.output else (base_dir / f"{args.ticker}.xlsx")

    if not narrative_path.exists():
        sys.exit(f"ERROR: narrative not found: {narrative_path}")
    if not valuation_path.exists():
        sys.exit(f"ERROR: valuation not found: {valuation_path}")

    with open(narrative_path, encoding="utf-8") as f:
        narrative = json.load(f)
    with open(valuation_path, encoding="utf-8") as f:
        valuation = json.load(f)

    wb = Workbook()
    # Remove default sheet
    del wb["Sheet"]

    build_cover(wb, narrative, valuation)
    build_assumptions(wb, valuation)
    build_calculation(wb, valuation)
    build_formula_trace(wb, valuation)
    build_sensitivity(wb, valuation)
    build_scenarios(wb, valuation)
    build_peers(wb, narrative)
    build_reasoning_log(wb, narrative, valuation)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    print(f"[OK] wrote {output_path}")


if __name__ == "__main__":
    main()
