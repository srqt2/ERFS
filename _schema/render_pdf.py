"""Render a locked-template PDF report from the narrative + valuation JSONs.

Same JSON in -> same PDF out. The agent does not touch PDF layout.

Usage:
    python render_pdf.py --ticker AVGO --category AI
       reads:  output/AVGO/AVGO.json + output/AVGO/AVGO_valuation.json
       writes: output/AVGO/AVGO.pdf

Layout (locked sections in order):
    1. Cover page         - rating banner, key metrics grid, 3-bullet thesis
    2. Valuation page     - WACC + adjudication + FCFF build + dual TV (or DDM build for banks)
    3. Scenarios page     - probability-weighted Bear/Base/Bull + blended target
    4. Bull / bear page   - top catalysts and thesis-breakers
    5. Peers + cross-check page
    6. Risks page         - key structural risks
    7. Methodology page   - assumptions log + data gaps
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, KeepTogether, Image
    )
except ImportError:
    sys.exit("ERROR: reportlab not installed. Run: pip install reportlab")


# ============================================================
# COLORS + STYLES
# ============================================================

C_INK = colors.HexColor("#111827")
C_MUTED = colors.HexColor("#6B7280")
C_LIGHT = colors.HexColor("#F3F4F6")
C_LIGHTER = colors.HexColor("#FAFAFA")
C_BORDER = colors.HexColor("#E5E7EB")
C_GOOD = colors.HexColor("#166534")
C_GOOD_BG = colors.HexColor("#DCFCE7")
C_BAD = colors.HexColor("#991B1B")
C_BAD_BG = colors.HexColor("#FEE2E2")
C_NEUTRAL = colors.HexColor("#854D0E")
C_NEUTRAL_BG = colors.HexColor("#FEF3C7")
C_HILITE = colors.HexColor("#DBEAFE")
C_HEADER = colors.HexColor("#111827")


def get_styles():
    """Build the locked stylesheet."""
    ss = getSampleStyleSheet()

    styles = {
        "title": ParagraphStyle("title", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=22, leading=26, textColor=C_INK, spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontName="Helvetica",
                                   fontSize=10, leading=13, textColor=C_MUTED, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=14, leading=18, textColor=C_INK, spaceBefore=10, spaceAfter=4),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, textColor=C_INK, spaceBefore=8, spaceAfter=2,
                             textTransform="uppercase"),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=10, leading=14, textColor=C_INK),
        "body_bold": ParagraphStyle("body_bold", parent=ss["Normal"], fontName="Helvetica-Bold",
                                    fontSize=10, leading=14, textColor=C_INK),
        "muted": ParagraphStyle("muted", parent=ss["Normal"], fontName="Helvetica",
                                fontSize=9, leading=12, textColor=C_MUTED),
        "thesis_bullet": ParagraphStyle("thesis_bullet", parent=ss["Normal"], fontName="Helvetica",
                                        fontSize=10.5, leading=14, textColor=C_INK,
                                        leftIndent=14, bulletIndent=0, spaceAfter=6),
        "rating_buy": ParagraphStyle("rating_buy", parent=ss["Normal"], fontName="Helvetica-Bold",
                                     fontSize=12, leading=14, textColor=C_GOOD, alignment=TA_CENTER),
        "rating_hold": ParagraphStyle("rating_hold", parent=ss["Normal"], fontName="Helvetica-Bold",
                                      fontSize=12, leading=14, textColor=C_NEUTRAL, alignment=TA_CENTER),
        "rating_sell": ParagraphStyle("rating_sell", parent=ss["Normal"], fontName="Helvetica-Bold",
                                      fontSize=12, leading=14, textColor=C_BAD, alignment=TA_CENTER),
        "footer": ParagraphStyle("footer", parent=ss["Normal"], fontName="Helvetica",
                                 fontSize=8, leading=10, textColor=C_MUTED, alignment=TA_CENTER),
    }
    return styles


def rating_style(tone, styles):
    return {"positive": styles["rating_buy"], "negative": styles["rating_sell"]}.get(tone, styles["rating_hold"])


def rating_bg(tone):
    return {"positive": C_GOOD_BG, "negative": C_BAD_BG}.get(tone, C_NEUTRAL_BG)


# ============================================================
# FORMATTERS
# ============================================================

def fmt_currency(v, currency):
    if v is None:
        return "n/a"
    if currency == "IDR":
        return f"IDR {v:,.0f}"
    if abs(v) >= 100:
        return f"${v:,.0f}"
    return f"${v:,.2f}"


def fmt_bn(v, currency="USD"):
    if v is None:
        return "n/a"
    if currency == "IDR":
        return f"IDR {v:,.1f}T"
    return f"${v:,.1f}B"


def fmt_pct(v, decimals=1):
    if v is None:
        return "n/a"
    return f"{v*100:.{decimals}f}%"


def fmt_signed_pct(v, decimals=1):
    if v is None:
        return "n/a"
    return f"{v:+.{decimals}f}%"


# ============================================================
# HEADER / FOOTER
# ============================================================

def _draw_header_footer(ticker, name, styles):
    def _h(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(C_INK)
        canvas.drawString(2 * cm, A4[1] - 1.2 * cm, f"{ticker}  —  {name}")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(C_MUTED)
        canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 1.2 * cm, "IntelliDesk · Equity Research")
        canvas.line(2 * cm, A4[1] - 1.4 * cm, A4[0] - 2 * cm, A4[1] - 1.4 * cm)
        # Footer
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(C_MUTED)
        canvas.drawString(2 * cm, 1.2 * cm, "Generated by render_pdf.py from JSON. For research only. Not financial advice.")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.restoreState()
    return _h


# ============================================================
# COVER PAGE
# ============================================================

def build_cover_page(story, narrative, valuation, styles):
    ticker = narrative["ticker"]
    name = narrative["name"]
    rec = narrative.get("recommendation", {})
    currency = valuation.get("currency", "USD")
    snapshot = {s["label"]: s["value"] for s in narrative.get("snapshot", []) if isinstance(s, dict)}

    def snap(prefix, default="n/a"):
        for k, v in snapshot.items():
            if k.lower().startswith(prefix.lower()):
                return v
        return default

    story.append(Paragraph(f"{name.upper()} ({ticker})", styles["title"]))
    story.append(Paragraph(
        f"{narrative.get('listings', '')} · {narrative.get('sector', '')} · Date {narrative.get('date', '')}",
        styles["subtitle"]
    ))
    story.append(Spacer(1, 0.3 * cm))

    # Rating banner
    rating_table = Table(
        [[Paragraph(rec.get("action", ""), rating_style(rec.get("tone"), styles)),
          Paragraph(
              f"<b>{fmt_currency(rec.get('current_price'), currency)}</b> spot  "
              f"<font color=\"#6B7280\">→</font>  "
              f"<b>{fmt_currency(rec.get('target_12m'), currency)}</b> 12m target  "
              f"<font color=\"#6B7280\">({rec.get('upside_pct', 0):+.1f}%)</font>",
              styles["body"]),
        ]],
        colWidths=[5 * cm, 11.5 * cm]
    )
    rating_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), rating_bg(rec.get("tone"))),
        ("BACKGROUND", (1, 0), (1, 0), C_LIGHTER),
        ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(rating_table)
    story.append(Spacer(1, 0.5 * cm))

    # Key metrics grid (4 columns)
    primary = valuation.get("primary_method", {})
    primary_outputs = primary.get("outputs", {})

    rows = [
        ("Market cap", snap("market cap"), "Enterprise value", snap("enterprise")),
        ("FY revenue", snap("FY") or snap("revenue"), "FY EBITDA / margin", snap("EBITDA")),
        ("FY FCF", snap("FCF"), "Net debt", snap("Net debt")),
        ("Forward EPS", snap("Forward EPS"), "Forward P/E", snap("Forward P/E")),
        ("EV/EBITDA", snap("EV"), "Beta", snap("Beta")),
        ("ADTV (30d)", snap("ADTV"), "30d vol", snap("30d")),
        ("Analyst consensus", snap("consensus"), "Mean PT", snap("Mean") or snap("median")),
    ]
    if primary.get("name") == "DCF":
        comps = primary.get("inputs", {}).get("wacc", {}).get("components", {})
        wacc_value = primary.get("inputs", {}).get("wacc", {}).get("value")
        ke_value = comps.get("rf", 0) + comps.get("beta", 0) * comps.get("erp", 0)
        rows.append(("WACC", fmt_pct(wacc_value, 2), "Ke (CAPM)", fmt_pct(ke_value, 2)))
        rows.append(("DCF Base implied", fmt_currency(primary_outputs.get("implied_px"), currency),
                     "Math blended target", fmt_currency(valuation.get("blended_target"), currency)))
    elif primary.get("name") == "DDM":
        ke = primary_outputs.get("cost_of_equity_pct")
        ddm_val = primary_outputs.get("ddm", {}).get("implied_value")
        rows.append(("Cost of equity (Ke)", f"{ke:.2f}%" if ke is not None else "n/a",
                     "Method", "DDM"))
        rows.append(("DDM implied", fmt_currency(ddm_val, currency),
                     "Math blended target", fmt_currency(valuation.get("blended_target"), currency)))

    # Build the metrics table
    table_data = []
    for r in rows:
        table_data.append([
            Paragraph(f"<b>{r[0]}</b>", styles["body"]),
            Paragraph(str(r[1]), styles["body"]),
            Paragraph(f"<b>{r[2]}</b>", styles["body"]),
            Paragraph(str(r[3]), styles["body"]),
        ])
    metrics_table = Table(table_data, colWidths=[3.6 * cm, 4.7 * cm, 3.6 * cm, 4.7 * cm])
    metrics_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("BACKGROUND", (0, 0), (0, -1), C_LIGHTER),
        ("BACKGROUND", (2, 0), (2, -1), C_LIGHTER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 0.4 * cm))

    # Thesis
    story.append(Paragraph("THREE-LINE THESIS", styles["h3"]))
    for t in narrative.get("thesis", []):
        story.append(Paragraph(f"• {t}", styles["thesis_bullet"]))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        f"<i>Research cycle: equity-bull-case skill → equity-bear-case skill (independent) → "
        f"synthesis → {primary.get('name', 'valuation')} compute → render Excel + PDF</i>",
        styles["muted"]
    ))


# ============================================================
# VALUATION PAGE
# ============================================================

def build_valuation_page(story, narrative, valuation, styles):
    story.append(PageBreak())
    primary = valuation.get("primary_method", {})
    method = primary.get("name", "")
    currency = valuation.get("currency", "USD")
    outputs = primary.get("outputs", {})
    inputs = primary.get("inputs", {})

    story.append(Paragraph(f"VALUATION — {method}", styles["h2"]))
    story.append(Paragraph(
        f"Method reasoning: {primary.get('reasoning', '')}",
        styles["body"]
    ))
    story.append(Spacer(1, 0.3 * cm))

    if method == "DCF":
        # WACC summary
        story.append(Paragraph("WACC INPUTS & ADJUDICATION", styles["h3"]))
        comps = inputs.get("wacc", {}).get("components", {})
        rf = comps.get("rf", 0)
        beta = comps.get("beta", 0)
        erp = comps.get("erp", 0)
        debt_w = comps.get("debt_weight", 0)
        kd_at = comps.get("cost_of_debt_after_tax", 0)
        ke = rf + beta * erp
        wacc_value = inputs.get("wacc", {}).get("value", 0)

        wacc_data = [
            ["Input", "Source", "Value"],
            ["Rf (risk-free rate)", "10Y government bond", fmt_pct(rf, 2)],
            ["Beta", "yfinance / input", f"{beta:.3f}"],
            ["ERP", "Damodaran DM/EM", fmt_pct(erp, 2)],
            ["Ke = Rf + Beta × ERP", "CAPM", fmt_pct(ke, 2)],
            ["Debt weight (D/V)", "input", fmt_pct(debt_w, 1)],
            ["Equity weight (E/V)", "1 - D/V", fmt_pct(1 - debt_w, 1)],
            ["Kd (after-tax)", "input", fmt_pct(kd_at, 2)],
            ["Formula WACC", "(E/V × Ke) + (D/V × Kd)", fmt_pct(wacc_value, 2)],
        ]
        wacc_table = Table(wacc_data, colWidths=[5.5 * cm, 6 * cm, 4 * cm])
        wacc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("BACKGROUND", (0, -1), (-1, -1), C_HILITE),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(wacc_table)
        story.append(Spacer(1, 0.3 * cm))

        # Adjudication crosscheck
        adj = outputs.get("wacc_adjudication") or {}
        if adj:
            band = adj.get("sector_band", [0, 0])
            verdict = adj.get("verdict", "")
            verdict_color = C_NEUTRAL_BG if ("ABOVE" in verdict.upper() or "BELOW" in verdict.upper()) else C_GOOD_BG
            adj_data = [
                [Paragraph("<b>Sector band</b>", styles["body"]),
                 Paragraph(f"{adj.get('sector', 'default')}: {band[0]*100:.1f}% − {band[1]*100:.1f}%", styles["body"])],
                [Paragraph("<b>Verdict</b>", styles["body"]),
                 Paragraph(f"<b>{fmt_pct(wacc_value, 2)}</b> — {verdict}", styles["body"])],
            ]
            adj_table = Table(adj_data, colWidths=[3.5 * cm, 12 * cm])
            adj_table.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                ("BACKGROUND", (0, 0), (0, -1), C_LIGHTER),
                ("BACKGROUND", (1, -1), (1, -1), verdict_color),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(adj_table)
            story.append(Spacer(1, 0.4 * cm))

        # FCFF build
        fcff_build = outputs.get("fcff_build") or []
        if fcff_build and any(f.get("has_full_build") for f in fcff_build):
            story.append(Paragraph("FCFF BUILD (5-7 year explicit period)", styles["h3"]))
            years = [str(f.get("year", "")) for f in fcff_build]
            # Build pivot rows: metric x years
            metric_rows = [
                ("Revenue (B)", "revenue_b", None),
                ("EBIT margin", "ebit_margin", "pct"),
                ("EBIT (B)", "ebit_b", None),
                ("NOPAT (B)", "nopat_b", None),
                ("D&A (B)", "da_b", None),
                ("Capex (B)", "capex_b", None),
                ("dNWC (B)", "wc_change_b", None),
                ("FCFF (B)", "fcf_b", None),
            ]
            trace = outputs.get("calculation_trace", {})
            per_year = trace.get("explicit_fcf", {}).get("per_year", [])
            fcff_data = [["Item"] + years]
            for label, key, fmt in metric_rows:
                row = [label]
                for fb in fcff_build:
                    v = fb.get(key)
                    if v is None:
                        row.append("—")
                    elif fmt == "pct":
                        row.append(fmt_pct(v))
                    elif isinstance(v, float):
                        row.append(f"{v:,.1f}")
                    else:
                        row.append(str(v))
                fcff_data.append(row)
            if per_year:
                pv_row = ["PV (B)"] + [f"{py.get('pv_b', 0):,.1f}" for py in per_year]
                fcff_data.append(pv_row)

            n_years = len(years)
            col_widths = [3.2 * cm] + [(15.5 - 3.2) / n_years * cm] * n_years
            fcff_table = Table(fcff_data, colWidths=col_widths)
            fcff_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                ("BACKGROUND", (0, -1), (-1, -1), C_HILITE),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -2), (-1, -2), C_LIGHTER),  # FCFF row
                ("FONTNAME", (0, -2), (-1, -2), "Helvetica-Bold"),
                ("BACKGROUND", (0, 1), (0, -1), C_LIGHTER),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(fcff_table)
            story.append(Spacer(1, 0.4 * cm))

        # Terminal value & EV bridge
        story.append(Paragraph("TERMINAL VALUE & EV BRIDGE", styles["h3"]))
        dual_tv = outputs.get("dual_terminal_value") or {}
        tv_data = [
            ["Component", "Value (B)"],
            ["Terminal growth (g)", fmt_pct(inputs.get("terminal_growth", {}).get("value"), 2)],
            ["TV — Gordon Growth", f"{dual_tv.get('gordon_tv_b', 0):,.1f}" if dual_tv.get("gordon_tv_b") else "n/a"],
        ]
        if dual_tv.get("exit_multiple_tv_b") is not None:
            tv_data.append([f"TV — Exit Multiple ({dual_tv.get('exit_multiple_x')}× EBITDA)",
                            f"{dual_tv.get('exit_multiple_tv_b', 0):,.1f}"])
            tv_data.append(["TV — Blended (50/50)", f"{dual_tv.get('blended_tv_b', 0):,.1f}"])
        tv_data += [
            ["PV of TV (Gordon)", f"{dual_tv.get('gordon_pv_b', 0):,.1f}" if dual_tv.get("gordon_pv_b") else "n/a"],
            ["Sum of PV of explicit FCFF", f"{outputs.get('pv_explicit_fcf_b', 0):,.1f}"],
            ["Implied EV", f"{outputs.get('implied_ev_b', 0):,.1f}"],
            ["(less) Net debt", f"{valuation.get('net_debt_b', 0):,.1f}"],
            ["Implied equity value", f"{outputs.get('implied_equity_b', 0):,.1f}"],
            ["Diluted shares (B)", f"{valuation.get('shares_outstanding_b', 0):,.2f}"],
            ["IMPLIED PRICE / SHARE", fmt_currency(outputs.get("implied_px"), currency)],
        ]
        tv_table = Table(tv_data, colWidths=[10 * cm, 5.5 * cm])
        tv_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("BACKGROUND", (0, -1), (-1, -1), C_HILITE),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, -3), (-1, -3), C_LIGHTER),
            ("FONTNAME", (0, -3), (-1, -3), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tv_table)

    elif method == "DDM":
        outputs_ddm = outputs.get("ddm", {})
        outputs_er = outputs.get("excess_returns", {})
        outputs_jpb = outputs.get("justified_pb", {})
        ddm_data = [
            ["Component", "Implied value"],
            ["Cost of equity (Ke)", fmt_pct(outputs.get("cost_of_equity_pct", 0) / 100, 2)],
            ["DDM implied", fmt_currency(outputs_ddm.get("implied_value"), currency)],
            ["Excess Returns implied", fmt_currency(outputs_er.get("implied_value"), currency)],
            ["Justified P/B implied", fmt_currency(outputs_jpb.get("implied_value"), currency)],
            ["Blended (40/40/20)", fmt_currency(outputs.get("implied_px"), currency)],
        ]
        ddm_table = Table(ddm_data, colWidths=[10 * cm, 5.5 * cm])
        ddm_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("BACKGROUND", (0, -1), (-1, -1), C_HILITE),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(ddm_table)


# ============================================================
# SCENARIOS PAGE
# ============================================================

def build_scenarios_page(story, narrative, valuation, styles):
    story.append(PageBreak())
    currency = valuation.get("currency", "USD")
    story.append(Paragraph("SCENARIOS & PROBABILITY WEIGHTS", styles["h2"]))
    story.append(Paragraph(
        "Final target = Σ(scenario.prob × scenario.implied_px) + cross_check.weight × cross_check.implied_px",
        styles["muted"]
    ))
    story.append(Spacer(1, 0.3 * cm))

    scenarios = valuation.get("scenarios", [])
    cc = valuation.get("cross_check")
    cc_weight = (valuation.get("blending_weights") or {}).get("cross_check", 0)

    # Table header
    rows = [["Scenario", "Implied px", "Prob.", "Contribution"]]
    body_rows_styles = []
    row_idx = 1
    for scen in scenarios:
        prob = scen.get("probability")
        contrib = round(prob * scen["implied_px"], 2) if (prob and scen.get("implied_px")) else None
        rows.append([
            scen["label"],
            fmt_currency(scen.get("implied_px"), currency),
            fmt_pct(prob, 1) if prob is not None else "—",
            fmt_currency(contrib, currency) if contrib is not None else "—",
        ])
        if scen["label"].lower() == "bull":
            body_rows_styles.append(("BACKGROUND", (0, row_idx), (0, row_idx), C_GOOD_BG))
        elif scen["label"].lower() == "bear":
            body_rows_styles.append(("BACKGROUND", (0, row_idx), (0, row_idx), C_BAD_BG))
        else:
            body_rows_styles.append(("BACKGROUND", (0, row_idx), (0, row_idx), C_NEUTRAL_BG))
        row_idx += 1
    if cc and cc_weight > 0:
        cc_px = cc.get("outputs", {}).get("implied_px")
        contrib = round(cc_weight * cc_px, 2) if cc_px else None
        rows.append([
            cc.get("name", "Cross-check"),
            fmt_currency(cc_px, currency),
            fmt_pct(cc_weight, 1),
            fmt_currency(contrib, currency) if contrib is not None else "—",
        ])
        body_rows_styles.append(("BACKGROUND", (0, row_idx), (0, row_idx), C_HILITE))
        row_idx += 1

    # Weight total + blended target
    weight_total = valuation.get("weight_total_check") or (
        sum((s.get("probability") or 0) for s in scenarios) + cc_weight)
    rows.append(["WEIGHT TOTAL", "", fmt_pct(weight_total, 1), ""])
    rows.append(["BLENDED TARGET", "", "", fmt_currency(valuation.get("blended_target"), currency)])
    cp = valuation.get("current_price")
    upside = valuation.get("upside_pct")
    if cp:
        rows.append(["Current price", fmt_currency(cp, currency), "", ""])
        if upside is not None:
            rows.append(["Upside vs spot", "", "", fmt_signed_pct(upside)])

    scen_table = Table(rows, colWidths=[5 * cm, 3.5 * cm, 2.5 * cm, 4.5 * cm])
    base_style = [
        ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    base_style += body_rows_styles
    # Highlight blended target row
    blended_row = len(rows) - (3 if cp else 1)
    base_style.append(("BACKGROUND", (0, blended_row), (-1, blended_row), C_HILITE))
    base_style.append(("FONTNAME", (0, blended_row), (-1, blended_row), "Helvetica-Bold"))
    scen_table.setStyle(TableStyle(base_style))
    story.append(scen_table)
    story.append(Spacer(1, 0.4 * cm))

    # Why these weights
    weights_reasoning = valuation.get("weights_reasoning") or valuation.get("blending_logic", "")
    if weights_reasoning:
        story.append(Paragraph("WHY THESE WEIGHTS?", styles["h3"]))
        story.append(Paragraph(weights_reasoning, styles["body"]))


# ============================================================
# BULL / BEAR PAGE
# ============================================================

def build_bull_bear_page(story, narrative, styles):
    story.append(PageBreak())
    story.append(Paragraph("BULL CASE", styles["h2"]))
    for c in narrative.get("bull_catalysts", [])[:6]:
        story.append(Paragraph(f"<b>{c.get('id', '')}. {c.get('title', '')}</b>", styles["body_bold"]))
        story.append(Paragraph(c.get("body", ""), styles["body"]))
        story.append(Spacer(1, 0.2 * cm))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("BEAR CASE", styles["h2"]))
    for c in narrative.get("bear_breakers", [])[:6]:
        story.append(Paragraph(f"<b>{c.get('id', '')}. {c.get('title', '')}</b>", styles["body_bold"]))
        story.append(Paragraph(c.get("body", ""), styles["body"]))
        story.append(Spacer(1, 0.2 * cm))

    if narrative.get("bear_paragraph"):
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("ONE-PARAGRAPH BEAR", styles["h3"]))
        story.append(Paragraph(narrative["bear_paragraph"], styles["body"]))


# ============================================================
# PEERS PAGE
# ============================================================

def build_peers_page(story, narrative, valuation, styles):
    story.append(PageBreak())
    story.append(Paragraph("PEER COMPARABLES & CROSS-CHECK", styles["h2"]))
    peers = narrative.get("peers", [])
    if peers:
        rows = [["Ticker", "NTM P/E", "EV/EBITDA", "Rev Growth", "YTD", "1Y"]]
        highlight_rows = []
        for p in peers:
            rows.append([
                p["ticker"],
                f"{p.get('pe_ntm', 0):.1f}x" if p.get("pe_ntm") is not None else "—",
                f"{p.get('ev_ebitda', 0):.1f}x" if p.get("ev_ebitda") is not None else "—",
                fmt_pct(p.get("rev_growth_ttm", 0) / 100 if p.get("rev_growth_ttm") is not None else None),
                fmt_pct(p.get("ytd", 0) / 100 if p.get("ytd") is not None else None),
                fmt_pct(p.get("y1", 0) / 100 if p.get("y1") is not None else None),
            ])
            if p.get("highlight"):
                highlight_rows.append(len(rows) - 1)
        peers_table = Table(rows, colWidths=[2.5 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm])
        base_style = [
            ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for hr in highlight_rows:
            base_style.append(("BACKGROUND", (0, hr), (-1, hr), C_HILITE))
            base_style.append(("FONTNAME", (0, hr), (-1, hr), "Helvetica-Bold"))
        peers_table.setStyle(TableStyle(base_style))
        story.append(peers_table)
        story.append(Spacer(1, 0.3 * cm))

    if narrative.get("peers_read"):
        story.append(Paragraph("READ", styles["h3"]))
        story.append(Paragraph(narrative["peers_read"], styles["body"]))

    cc = valuation.get("cross_check")
    if cc:
        story.append(Spacer(1, 0.4 * cm))
        story.append(Paragraph("CROSS-CHECK METHOD", styles["h3"]))
        cc_inputs = cc.get("inputs", {})
        cc_outputs = cc.get("outputs", {})
        cc_text = (
            f"<b>{cc.get('name', '')}</b><br/>"
            f"{cc.get('reasoning', '')}<br/><br/>"
            f"Multiple: <b>{cc_inputs.get('peer_median_multiple', 'n/a')}× {cc_inputs.get('multiple_type', '')}</b>  "
            f"·  Estimate: {cc_inputs.get('fy_estimate', 'n/a')}  "
            f"·  Implied price: <b>{fmt_currency(cc_outputs.get('implied_px'), valuation.get('currency', 'USD'))}</b>"
        )
        story.append(Paragraph(cc_text, styles["body"]))


# ============================================================
# RISKS PAGE
# ============================================================

def build_risks_page(story, narrative, styles):
    risks = narrative.get("key_risks") or []
    if not risks:
        return
    story.append(PageBreak())
    story.append(Paragraph("KEY STRUCTURAL RISKS", styles["h2"]))
    story.append(Paragraph(
        "Risks orthogonal to the bear thesis — what breaks the long term, not just the next print.",
        styles["muted"]
    ))
    story.append(Spacer(1, 0.3 * cm))

    cat_colors = {
        "structural": (C_BAD, C_BAD_BG),
        "cyclical": (C_NEUTRAL, C_NEUTRAL_BG),
        "regulatory": (colors.HexColor("#3730A3"), colors.HexColor("#E0E7FF")),
        "execution": (colors.HexColor("#9F1239"), colors.HexColor("#FCE7F3")),
        "customer": (colors.HexColor("#9A3412"), colors.HexColor("#FED7AA")),
        "macro": (colors.HexColor("#155E75"), colors.HexColor("#CFFAFE")),
    }
    for r in risks[:7]:
        cat = r.get("category", "structural").lower()
        fg, bg = cat_colors.get(cat, (C_INK, C_LIGHT))
        head_table = Table(
            [[Paragraph(f"<font color=\"{fg.hexval()}\"><b>{cat.upper()}</b></font>", styles["body"]),
              Paragraph(f"<b>{r.get('title', '')}</b>", styles["body"])]],
            colWidths=[3.0 * cm, 12.5 * cm]
        )
        head_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), bg),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(head_table)
        story.append(Paragraph(r.get("description", ""), styles["body"]))
        story.append(Spacer(1, 0.25 * cm))


# ============================================================
# METHODOLOGY / REASONING PAGE
# ============================================================

def build_methodology_page(story, narrative, valuation, styles):
    story.append(PageBreak())
    story.append(Paragraph("METHODOLOGY & REASONING LOG", styles["h2"]))
    primary = valuation.get("primary_method", {})
    inputs = primary.get("inputs", {})

    sections = []
    sections.append(("Valuation method", f"{primary.get('name')} — {primary.get('reasoning', '')}"))
    if primary.get("name") == "DCF":
        wacc = inputs.get("wacc", {})
        sections.append(("WACC reasoning", wacc.get("reasoning", "")))
        sections.append(("Terminal growth reasoning", inputs.get("terminal_growth", {}).get("reasoning", "")))
    elif primary.get("name") == "DDM":
        for key, label in [("rf_reasoning", "Rf"), ("erp_reasoning", "ERP"),
                            ("beta_reasoning", "Beta"), ("d0_reasoning", "Sustainable D0"),
                            ("g_terminal_reasoning", "Terminal growth"), ("g_high_reasoning", "High-growth period"),
                            ("g_book_reasoning", "Book value growth")]:
            r_text = inputs.get(key, "")
            if r_text:
                sections.append((label, r_text))
    cc = valuation.get("cross_check")
    if cc:
        sections.append(("Cross-check", f"{cc.get('name')} — {cc.get('reasoning', '')}"))
    sections.append(("Weights reasoning",
                     valuation.get("weights_reasoning") or valuation.get("blending_logic", "")))

    if narrative.get("data_gaps"):
        sections.append(("Data gaps from this cycle", " | ".join(narrative["data_gaps"])))

    for label, value in sections:
        story.append(Paragraph(label.upper(), styles["h3"]))
        story.append(Paragraph(value, styles["body"]))
        story.append(Spacer(1, 0.2 * cm))


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--category", required=True, help="AI or IDX")
    parser.add_argument("--narrative", help="Path to narrative JSON")
    parser.add_argument("--valuation", help="Path to valuation JSON")
    parser.add_argument("--output", help="Path to output PDF")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent / "output" / args.ticker
    narrative_path = Path(args.narrative) if args.narrative else (base_dir / f"{args.ticker}.json")
    valuation_path = Path(args.valuation) if args.valuation else (base_dir / f"{args.ticker}_valuation.json")
    output_path = Path(args.output) if args.output else (base_dir / f"{args.ticker}.pdf")

    if not narrative_path.exists():
        sys.exit(f"ERROR: narrative not found: {narrative_path}")
    if not valuation_path.exists():
        sys.exit(f"ERROR: valuation not found: {valuation_path}")

    with open(narrative_path, encoding="utf-8") as f:
        narrative = json.load(f)
    with open(valuation_path, encoding="utf-8") as f:
        valuation = json.load(f)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = get_styles()

    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=1.8 * cm,
        title=f"{narrative['ticker']} — {narrative['name']}",
        author="IntelliDesk Equity Research",
    )

    story = []
    build_cover_page(story, narrative, valuation, styles)
    build_valuation_page(story, narrative, valuation, styles)
    build_scenarios_page(story, narrative, valuation, styles)
    build_bull_bear_page(story, narrative, styles)
    build_peers_page(story, narrative, valuation, styles)
    build_risks_page(story, narrative, styles)
    build_methodology_page(story, narrative, valuation, styles)

    handler = _draw_header_footer(narrative["ticker"], narrative["name"], styles)
    doc.build(story, onFirstPage=handler, onLaterPages=handler)
    print(f"[OK] wrote {output_path}")


if __name__ == "__main__":
    main()
