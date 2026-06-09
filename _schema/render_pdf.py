"""Render a locked-template research report PDF from the narrative + valuation JSONs.

This produces the LONG-FORM ANALYSIS report (thesis, business overview, catalysts,
bear case, peers, risks, recommendation) — mirroring exactly what's published on
the IntelliDesk Vercel page at /research/<category>/<ticker>.

The Excel renderer (render_excel.py) produces the VALUATION MODEL workbook.
These are two distinct deliverables; this script makes the research report.

Same JSON in → same PDF out. The agent does not touch PDF layout.

Usage:
    python render_pdf.py --ticker AVGO --category AI
       reads:  output/AVGO/AVGO.json + output/AVGO/AVGO_valuation.json
       writes: output/AVGO/AVGO.pdf

Sections (locked order, mirrors Vercel page):
    1. Header + Recommendation banner
    2. Thesis (3 bullets)
    3. Key numbers (snapshot grid)
    4. Business overview (summary, business model, segments, customers, geographic)
    5. Historical financials (multi-year P&L table)
    6. Industry & competitive position (market overview, TAM, moat, competitors)
    7. Bull catalysts (8-10 numbered cards)
    8. Bear thesis-breakers (8-10 numbered cards)
    9. Bear paragraph callout
    10. Key structural risks (5-7 by category)
    11. Peers table + read
    12. Valuation summary (DCF triangulation or DDM/Bank summary)
    13. Synthesis paths (3 probability-weighted)
    14. Management & capital allocation
    15. Social sentiment (if available)
    16. Recommendation table
    17. Catalysts to watch
    18. Data gaps
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
        PageBreak, KeepTogether
    )
except ImportError:
    sys.exit("ERROR: reportlab not installed. Run: pip install reportlab")


# ============================================================
# COLOR PALETTE — matches Vercel detail page
# ============================================================

C_INK = colors.HexColor("#111827")
C_TEXT = colors.HexColor("#1F2937")
C_MUTED = colors.HexColor("#6B7280")
C_LIGHT = colors.HexColor("#F3F4F6")
C_LIGHTER = colors.HexColor("#FAFAFA")
C_BORDER = colors.HexColor("#E5E7EB")
C_BG = colors.HexColor("#F9FAFB")

C_GOOD = colors.HexColor("#166534")
C_GOOD_BG = colors.HexColor("#DCFCE7")
C_BAD = colors.HexColor("#991B1B")
C_BAD_BG = colors.HexColor("#FEE2E2")
C_NEUTRAL = colors.HexColor("#854D0E")
C_NEUTRAL_BG = colors.HexColor("#FEF3C7")
C_HILITE = colors.HexColor("#DBEAFE")
C_HEADER = colors.HexColor("#111827")

# Risk category color map
RISK_COLORS = {
    "structural": (C_BAD, C_BAD_BG),
    "cyclical":   (C_NEUTRAL, C_NEUTRAL_BG),
    "regulatory": (colors.HexColor("#3730A3"), colors.HexColor("#E0E7FF")),
    "execution":  (colors.HexColor("#9F1239"), colors.HexColor("#FCE7F3")),
    "customer":   (colors.HexColor("#9A3412"), colors.HexColor("#FED7AA")),
    "macro":      (colors.HexColor("#155E75"), colors.HexColor("#CFFAFE")),
}


# ============================================================
# STYLES
# ============================================================

def get_styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=20, leading=24, textColor=C_INK, spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontName="Helvetica",
                                   fontSize=9.5, leading=12, textColor=C_MUTED, spaceAfter=6),
        "section": ParagraphStyle("section", parent=ss["Heading2"], fontName="Helvetica-Bold",
                                  fontSize=12, leading=16, textColor=C_INK,
                                  spaceBefore=10, spaceAfter=2,
                                  borderPadding=4, borderRadius=4),
        "section_subtitle": ParagraphStyle("section_subtitle", parent=ss["Normal"], fontName="Helvetica",
                                           fontSize=9, leading=11, textColor=C_MUTED, spaceAfter=6,
                                           italic=True),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontName="Helvetica-Bold",
                             fontSize=10.5, leading=13, textColor=C_INK,
                             spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=13, textColor=C_TEXT),
        "body_justified": ParagraphStyle("body_j", parent=ss["Normal"], fontName="Helvetica",
                                         fontSize=9.5, leading=13, textColor=C_TEXT,
                                         alignment=TA_JUSTIFY),
        "body_bold": ParagraphStyle("body_bold", parent=ss["Normal"], fontName="Helvetica-Bold",
                                    fontSize=9.5, leading=13, textColor=C_INK),
        "muted": ParagraphStyle("muted", parent=ss["Normal"], fontName="Helvetica",
                                fontSize=8.5, leading=11, textColor=C_MUTED),
        "card_title": ParagraphStyle("card_title", parent=ss["Normal"], fontName="Helvetica-Bold",
                                     fontSize=10, leading=12, textColor=C_INK, spaceAfter=2),
        "card_body": ParagraphStyle("card_body", parent=ss["Normal"], fontName="Helvetica",
                                    fontSize=9, leading=12, textColor=C_TEXT,
                                    alignment=TA_JUSTIFY),
        "thesis_bullet": ParagraphStyle("thesis_bullet", parent=ss["Normal"], fontName="Helvetica",
                                        fontSize=10, leading=14, textColor=C_TEXT,
                                        leftIndent=12, bulletIndent=0, spaceAfter=4),
        "rec_action": ParagraphStyle("rec_action", parent=ss["Normal"], fontName="Helvetica-Bold",
                                     fontSize=13, leading=16, textColor=C_INK, alignment=TA_CENTER),
        "footer": ParagraphStyle("footer", parent=ss["Normal"], fontName="Helvetica",
                                 fontSize=7.5, leading=9, textColor=C_MUTED, alignment=TA_CENTER),
    }


def tone_color(tone):
    return {"positive": (C_GOOD, C_GOOD_BG), "negative": (C_BAD, C_BAD_BG)}.get(tone, (C_NEUTRAL, C_NEUTRAL_BG))


# ============================================================
# FORMATTERS
# ============================================================

def fmt_currency(v, currency):
    if v is None:
        return "—"
    if currency == "IDR":
        return f"IDR {v:,.0f}"
    if abs(v) >= 100:
        return f"${v:,.0f}"
    return f"${v:,.2f}"


def fmt_pct(v, decimals=1):
    if v is None:
        return "—"
    return f"{v:.{decimals}f}%"


def fmt_signed(v, decimals=1):
    if v is None:
        return "—"
    return f"{v:+.{decimals}f}%"


# ============================================================
# HEADER / FOOTER
# ============================================================

def _draw_header_footer(ticker, name):
    def _h(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.setFillColor(C_INK)
        canvas.drawString(2 * cm, A4[1] - 1.1 * cm, f"{ticker}  ·  {name}")
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(C_MUTED)
        canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 1.1 * cm, "IntelliDesk · Equity Research")
        canvas.setStrokeColor(C_BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(2 * cm, A4[1] - 1.3 * cm, A4[0] - 2 * cm, A4[1] - 1.3 * cm)
        # Footer
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(C_MUTED)
        canvas.drawString(2 * cm, 1.1 * cm,
                          "Engine: himself65/finance-skills + custom DCF/DDM + render_pdf.py · For research only · Not financial advice")
        canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"Page {doc.page}")
        canvas.restoreState()
    return _h


# ============================================================
# 1. HEADER + RECOMMENDATION BANNER
# ============================================================

def add_header(story, narrative, valuation, styles):
    ticker = narrative["ticker"]
    rec = narrative.get("recommendation", {})
    currency = valuation.get("currency", "USD")
    fg, bg = tone_color(rec.get("tone"))

    # Ticker + name title row
    story.append(Paragraph(
        f"<font color='{C_INK.hexval()}'><b>{ticker}</b></font>  "
        f"<font color='{C_TEXT.hexval()}'>{narrative.get('name', '')}</font>",
        styles["title"]
    ))
    story.append(Paragraph(
        f"{narrative.get('listings', '')} · {narrative.get('sector', '')} · "
        f"Updated <b>{narrative.get('date', '')}</b>",
        styles["subtitle"]
    ))
    story.append(Spacer(1, 0.2 * cm))

    # Recommendation banner — 4-cell grid like the Vercel page
    upside_pct = rec.get("upside_pct", 0)
    next_earn = rec.get("next_earnings", "—")
    if len(next_earn) > 50:
        next_earn = next_earn[:47] + "…"
    banner_data = [[
        Paragraph(f"<font color='{fg.hexval()}'><b>RECOMMENDATION</b></font><br/>"
                  f"<font color='{fg.hexval()}' size='11'><b>{rec.get('action', '')}</b></font>",
                  styles["body"]),
        Paragraph(f"<font color='{fg.hexval()}'><b>CURRENT PRICE</b></font><br/>"
                  f"<font color='{fg.hexval()}' size='11'><b>{fmt_currency(rec.get('current_price'), currency)}</b></font>",
                  styles["body"]),
        Paragraph(f"<font color='{fg.hexval()}'><b>12M TARGET</b></font><br/>"
                  f"<font color='{fg.hexval()}' size='11'><b>{fmt_currency(rec.get('target_12m'), currency)}</b></font>  "
                  f"<font color='{fg.hexval()}' size='8'>({upside_pct:+.1f}%)</font>",
                  styles["body"]),
        Paragraph(f"<font color='{fg.hexval()}'><b>NEXT EARNINGS</b></font><br/>"
                  f"<font color='{fg.hexval()}' size='9'><b>{next_earn}</b></font>",
                  styles["body"]),
    ]]
    t = Table(banner_data, colWidths=[4 * cm, 4 * cm, 4.5 * cm, 4 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.5, fg),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 2. THESIS
# ============================================================

def add_thesis(story, narrative, styles):
    if not narrative.get("thesis"):
        return
    _section(story, "Thesis", "Three bullets — the whole bet", styles)
    for i, t in enumerate(narrative["thesis"], start=1):
        story.append(Paragraph(f"<b>{i}.</b>  {t}", styles["thesis_bullet"]))
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 3. KEY NUMBERS (snapshot grid)
# ============================================================

def add_key_numbers(story, narrative, styles):
    snapshot = narrative.get("snapshot") or []
    if not snapshot:
        return
    _section(story, "Key numbers", "Pulled via yfinance-data · validated by Delta audit", styles)

    # Build a 3-col grid of (label / value) cells
    cells = []
    for s in snapshot:
        if isinstance(s, dict):
            cells.append(s.get("label", ""))
            cells.append(s.get("value", ""))

    # Group into rows of 6 columns (3 label-value pairs per row)
    rows = []
    n_per_row = 6  # 3 pairs of (label, value)
    current_row = []
    for i in range(0, len(cells), 2):
        label = cells[i] if i < len(cells) else ""
        value = cells[i + 1] if i + 1 < len(cells) else ""
        current_row.extend([
            Paragraph(f"<font color='{C_MUTED.hexval()}' size='7'>{label.upper()}</font>", styles["body"]),
            Paragraph(f"<b>{value}</b>", styles["body"]),
        ])
        if len(current_row) >= n_per_row:
            rows.append(current_row)
            current_row = []
    if current_row:
        # Pad
        while len(current_row) < n_per_row:
            current_row.append(Paragraph("", styles["body"]))
        rows.append(current_row)

    if rows:
        col_w = 16.5 / 6 * cm
        t = Table(rows, colWidths=[col_w] * 6)
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 4. BUSINESS OVERVIEW
# ============================================================

def add_business_overview(story, narrative, styles):
    bo = narrative.get("business_overview")
    if not bo:
        return
    _section(story, "Business overview", "What the company does, how it makes money, who it sells to", styles)

    if bo.get("summary"):
        story.append(Paragraph(bo["summary"], styles["body_justified"]))
        story.append(Spacer(1, 0.15 * cm))

    if bo.get("business_model"):
        _key_callout(story, "BUSINESS MODEL", bo["business_model"], styles)
        story.append(Spacer(1, 0.15 * cm))

    # Segments
    if bo.get("segments"):
        story.append(Paragraph("SEGMENTS", styles["h3"]))
        for seg in bo["segments"]:
            metrics_parts = []
            if seg.get("revenue_share_pct") is not None:
                metrics_parts.append(f"<font color='{C_MUTED.hexval()}'>rev</font> <b>{seg['revenue_share_pct']:.0f}%</b>")
            if seg.get("growth_yoy_pct") is not None:
                gc = C_GOOD if seg["growth_yoy_pct"] >= 0 else C_BAD
                metrics_parts.append(f"<font color='{gc.hexval()}'><b>{seg['growth_yoy_pct']:+.0f}% YoY</b></font>")
            if seg.get("margin_pct") is not None:
                metrics_parts.append(f"<font color='{C_MUTED.hexval()}'>mgn</font> <b>{seg['margin_pct']:.1f}%</b>")
            metrics = "  ·  ".join(metrics_parts)
            head = f"<b>{seg.get('name', '')}</b>" + (f"     {metrics}" if metrics else "")
            story.append(Paragraph(head, styles["body"]))
            if seg.get("description"):
                story.append(Paragraph(seg["description"], styles["card_body"]))
            story.append(Spacer(1, 0.1 * cm))

    # Customers
    if bo.get("customers"):
        story.append(Paragraph("TOP CUSTOMERS", styles["h3"]))
        rows = []
        for c in bo["customers"][:8]:
            share = f"<b>{c.get('revenue_share_pct'):.0f}%</b>" if c.get("revenue_share_pct") is not None else ""
            rows.append([
                Paragraph(f"<b>{c.get('name', '')}</b>", styles["body"]),
                Paragraph(share, styles["body"]),
                Paragraph(c.get("importance", ""), styles["card_body"]),
            ])
        if rows:
            t = Table(rows, colWidths=[5 * cm, 1.5 * cm, 10 * cm])
            t.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.3, C_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t)

    # Geographic
    if bo.get("geographic_revenue"):
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph("GEOGRAPHIC REVENUE", styles["h3"]))
        chips = "  ".join([
            f"<b>{g.get('region', '')}</b> <font color='{C_MUTED.hexval()}'>{g.get('pct', 0):.0f}%</font>"
            for g in bo["geographic_revenue"]
        ])
        story.append(Paragraph(chips, styles["body"]))
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 5. HISTORICAL FINANCIALS
# ============================================================

def add_historical_financials(story, narrative, styles):
    hist = narrative.get("historical_financials") or []
    if not hist:
        return
    _section(story, "Historical financials", "P&L trajectory — revenue, EBITDA, FCF, EPS", styles)

    rows = [["FY", "Revenue ($B)", "Growth", "Gross mgn", "EBITDA ($M)", "EBITDA mgn", "Net inc ($M)", "FCF ($M)", "EPS"]]
    for h in hist:
        def num(k, fmt="{:,.0f}"):
            v = h.get(k)
            return "—" if v is None else fmt.format(v)
        rows.append([
            h.get("fy", ""),
            num("revenue_usd_b", "{:,.2f}"),
            fmt_signed(h.get("revenue_growth_yoy_pct")) if h.get("revenue_growth_yoy_pct") is not None else "—",
            fmt_pct(h.get("gross_margin_pct")) if h.get("gross_margin_pct") is not None else "—",
            num("ebitda_usd_m"),
            fmt_pct(h.get("ebitda_margin_pct")) if h.get("ebitda_margin_pct") is not None else "—",
            num("net_income_usd_m"),
            num("fcf_usd_m"),
            num("eps", "${:,.2f}"),
        ])
    col_widths = [1.4 * cm, 1.9 * cm, 1.4 * cm, 1.6 * cm, 1.9 * cm, 1.7 * cm, 1.9 * cm, 1.6 * cm, 1.4 * cm]
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 6. INDUSTRY & COMPETITIVE POSITION
# ============================================================

def add_industry_position(story, narrative, styles):
    ip = narrative.get("industry_position")
    if not ip:
        return
    _section(story, "Industry & competitive position", "Where the company sits · moat · competitors", styles)

    if ip.get("market_overview"):
        story.append(Paragraph(ip["market_overview"], styles["body_justified"]))
        story.append(Spacer(1, 0.15 * cm))

    if ip.get("tam_usd_bn"):
        story.append(Paragraph(
            f"<font color='{colors.HexColor('#3730A3').hexval()}'><b>TAM: ${ip['tam_usd_bn']}B</b></font>",
            styles["body"]
        ))
        story.append(Spacer(1, 0.1 * cm))

    if ip.get("moat"):
        _key_callout(story, "MOAT", ip["moat"], styles, color=C_GOOD, bg=C_GOOD_BG)
        story.append(Spacer(1, 0.15 * cm))

    if ip.get("competitors"):
        story.append(Paragraph("COMPETITORS", styles["h3"]))
        rows = []
        for c in ip["competitors"]:
            rows.append([
                Paragraph(f"<b>{c.get('name', '')}</b>", styles["body"]),
                Paragraph(c.get("description", ""), styles["card_body"]),
            ])
        t = Table(rows, colWidths=[3 * cm, 13.5 * cm])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 7. BULL CATALYSTS
# ============================================================

def add_bull_catalysts(story, narrative, styles):
    catalysts = narrative.get("bull_catalysts") or []
    if not catalysts:
        return
    _section(story, "Bull catalysts", "What needs to happen for the long thesis to play out", styles)
    for c in catalysts:
        _numbered_card(story, c, styles, color=C_GOOD, bg=C_GOOD_BG)
    story.append(Spacer(1, 0.2 * cm))


# ============================================================
# 8. BEAR THESIS-BREAKERS
# ============================================================

def add_bear_breakers(story, narrative, styles):
    breakers = narrative.get("bear_breakers") or []
    if not breakers:
        return
    _section(story, "Bear thesis-breakers",
             "Independent bear lane · not visible to the bull author until synthesis",
             styles)
    for c in breakers:
        _numbered_card(story, c, styles, color=C_BAD, bg=C_BAD_BG)
    story.append(Spacer(1, 0.2 * cm))

    bp = narrative.get("bear_paragraph")
    if bp:
        story.append(Paragraph(
            "<b><font color='#991B1B'>BEAR CASE IN ONE PARAGRAPH</font></b>",
            styles["h3"]
        ))
        story.append(_callout_paragraph(bp, styles, color=C_BAD, bg=colors.HexColor("#FEF2F2")))
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 9. KEY STRUCTURAL RISKS
# ============================================================

def add_key_risks(story, narrative, styles):
    risks = narrative.get("key_risks") or []
    if not risks:
        return
    _section(story, "Key structural risks",
             "Risks orthogonal to the bear thesis · what breaks the long term, not just the next print",
             styles)
    for r in risks:
        cat = (r.get("category") or "structural").lower()
        fg, bg = RISK_COLORS.get(cat, (C_INK, C_LIGHT))
        head_table = Table(
            [[Paragraph(f"<font color='{fg.hexval()}'><b>{cat.upper()}</b></font>", styles["body"]),
              Paragraph(f"<b>{r.get('title', '')}</b>", styles["body"])]],
            colWidths=[2.5 * cm, 14 * cm]
        )
        head_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), bg),
            ("BOX", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(head_table)
        if r.get("description"):
            story.append(Paragraph(r["description"], styles["card_body"]))
        story.append(Spacer(1, 0.15 * cm))
    story.append(Spacer(1, 0.2 * cm))


# ============================================================
# 10. PEERS
# ============================================================

def add_peers(story, narrative, styles):
    peers = narrative.get("peers") or []
    if not peers:
        return
    _section(story, "Peers",
             "NTM P/E, EV/EBITDA, growth, and YTD / 1Y returns across the peer set",
             styles)

    rows = [["Ticker", "NTM P/E", "EV/EBITDA", "Rev growth", "YTD", "1Y"]]
    highlight_rows = []
    for p in peers:
        rows.append([
            p["ticker"],
            f"{p.get('pe_ntm', 0):.1f}x" if p.get("pe_ntm") is not None else "—",
            f"{p.get('ev_ebitda', 0):.1f}x" if p.get("ev_ebitda") is not None else "—",
            fmt_signed(p.get("rev_growth_ttm")) if p.get("rev_growth_ttm") is not None else "—",
            fmt_signed(p.get("ytd")) if p.get("ytd") is not None else "—",
            fmt_signed(p.get("y1")) if p.get("y1") is not None else "—",
        ])
        if p.get("highlight"):
            highlight_rows.append(len(rows) - 1)

    t = Table(rows, colWidths=[2.5 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm])
    base_style = [
        ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
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
    t.setStyle(TableStyle(base_style))
    story.append(t)

    if narrative.get("peers_read"):
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph(
            f"<font color='{C_MUTED.hexval()}'><b>Read:</b></font> {narrative['peers_read']}",
            styles["body"]
        ))
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 11. VALUATION SUMMARY (DCF triangulation or DDM)
# ============================================================

def add_valuation_summary(story, narrative, valuation, styles):
    primary = valuation.get("primary_method", {})
    method = primary.get("name")
    if not method:
        return
    currency = valuation.get("currency", "USD")

    if method == "DCF":
        _section(story, "DCF triangulation",
                 "Bear / Base / Bull scenarios · WACC × terminal growth × FCF growth path",
                 styles)
        scenarios = valuation.get("scenarios", [])
        rows = [["Scenario", "WACC", "Terminal g", "FCF path", "Implied px"]]
        for s in scenarios:
            kc = s.get("key_changes", {}) or {}
            wacc = kc.get("wacc")
            tg = kc.get("terminal_g") or kc.get("terminal_growth")
            fcf_mult = kc.get("fcf_multiplier")
            rows.append([
                s.get("label", ""),
                fmt_pct(wacc * 100, 1) if wacc else "(base)",
                fmt_pct(tg * 100, 1) if tg else "(base)",
                f"×{fcf_mult:.2f}" if fcf_mult else "(base FCF)",
                fmt_currency(s.get("implied_px"), currency),
            ])
        # Current price row
        rows.append(["Current price", "", "", "", fmt_currency(valuation.get("current_price"), currency)])
        t = Table(rows, colWidths=[2.5 * cm, 2 * cm, 2 * cm, 6.5 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("BACKGROUND", (0, -1), (-1, -1), C_LIGHTER),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ]))
        story.append(t)
    elif method == "DDM":
        _section(story, "Bank valuation (DDM)",
                 "Multi-stage DDM + Excess Returns + Justified P/B",
                 styles)
        outputs = primary.get("outputs", {}) or {}
        rows = [["Method", "Implied value"]]
        rows.append(["DDM", fmt_currency(outputs.get("ddm", {}).get("implied_value"), currency)])
        rows.append(["Excess Returns", fmt_currency(outputs.get("excess_returns", {}).get("implied_value"), currency)])
        jpb = outputs.get("justified_pb", {})
        rows.append([f"Justified P/B ({jpb.get('ratio'):.2f}x BV)" if jpb.get("ratio") else "Justified P/B",
                     fmt_currency(jpb.get("implied_value"), currency)])
        rows.append(["BLENDED (40/40/20)", fmt_currency(outputs.get("implied_px"), currency)])
        rows.append(["Current price", fmt_currency(valuation.get("current_price"), currency)])
        t = Table(rows, colWidths=[10 * cm, 6 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("BACKGROUND", (0, -2), (-1, -2), C_HILITE),
            ("FONTNAME", (0, -2), (-1, -2), "Helvetica-Bold"),
            ("BACKGROUND", (0, -1), (-1, -1), C_LIGHTER),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 12. SYNTHESIS PATHS
# ============================================================

def add_synthesis_paths(story, narrative, valuation, styles):
    paths = narrative.get("synthesis_paths") or []
    if not paths:
        # Fallback to valuation scenarios if narrative doesn't carry them
        paths_from_val = valuation.get("scenarios") or []
        if not paths_from_val:
            return
        rationale = valuation.get("blending_logic") or valuation.get("weights_reasoning", "")
    else:
        rationale = (narrative.get("recommendation") or {}).get("rationale", "")

    _section(story, "Synthesis — three paths",
             rationale if rationale else "Probability-weighted outcomes", styles)
    currency = valuation.get("currency", "USD")
    for p in paths:
        label = p.get("label", "")
        prob = p.get("prob") or p.get("probability")
        outcome = p.get("outcome_range") or fmt_currency(p.get("implied_px"), currency)
        desc = p.get("description") or p.get("reasoning", "")
        ll = label.lower()
        fg, bg = tone_color("positive" if ll == "bull" else "negative" if ll == "bear" else "neutral")
        head = Table(
            [[Paragraph(f"<font color='{fg.hexval()}'><b>{label}  ·  {outcome}</b></font>", styles["body"]),
              Paragraph(f"<font color='{fg.hexval()}'><b>{prob*100:.0f}% probability</b></font>" if prob else "",
                        styles["body"])]],
            colWidths=[10.5 * cm, 6 * cm]
        )
        head.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("BOX", (0, 0), (-1, -1), 0.3, fg),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(head)
        if desc:
            story.append(Paragraph(desc, styles["card_body"]))
        story.append(Spacer(1, 0.12 * cm))
    story.append(Spacer(1, 0.2 * cm))


# ============================================================
# 13. MANAGEMENT & CAPITAL ALLOCATION
# ============================================================

def add_management(story, narrative, styles):
    mgmt = narrative.get("management")
    if not mgmt:
        return
    _section(story, "Management & capital allocation",
             "Who runs the company · how they deploy capital · recent insider activity",
             styles)
    grid = []
    if mgmt.get("ceo"):
        grid.append(("CEO", mgmt["ceo"]))
    if mgmt.get("cfo"):
        grid.append(("CFO", mgmt["cfo"]))
    if mgmt.get("insider_ownership_pct") is not None:
        grid.append(("Insider ownership", f"{mgmt['insider_ownership_pct']:.1f}%"))
    if grid:
        rows = [[
            Paragraph(f"<font color='{C_MUTED.hexval()}' size='7'>{k.upper()}</font>", styles["body"]),
            Paragraph(f"<b>{v}</b>", styles["body"])
        ] for k, v in grid]
        t = Table(rows, colWidths=[3.5 * cm, 13 * cm])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.15 * cm))

    if mgmt.get("capital_allocation_history"):
        _key_callout(story, "CAPITAL ALLOCATION HISTORY", mgmt["capital_allocation_history"], styles)

    if mgmt.get("recent_insider_activity"):
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph("RECENT INSIDER ACTIVITY", styles["h3"]))
        rows = [["Date", "Insider", "Action", "Value"]]
        for a in mgmt["recent_insider_activity"]:
            val_m = a.get("value_usd_m", 0)
            rows.append([
                a.get("date", ""),
                a.get("insider", ""),
                a.get("action", ""),
                f"{'+' if val_m > 0 else ''}${abs(val_m):.1f}M",
            ])
        t = Table(rows, colWidths=[2.5 * cm, 5.5 * cm, 4 * cm, 4.5 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("ALIGN", (3, 1), (3, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 14. SOCIAL SENTIMENT (optional)
# ============================================================

def add_social_sentiment(story, narrative, styles):
    ss = narrative.get("social_sentiment")
    if not ss:
        return
    has_any = any(ss.get(k) is not None for k in ("bullish_pct", "x_mentions", "reddit_mentions", "summary"))
    if not has_any:
        return
    _section(story, "Social sentiment", "Cross-source signal · Reddit / X / news", styles)
    chips = []
    if ss.get("bullish_pct") is not None:
        chips.append(f"Bullish %: <b>{ss['bullish_pct']:.0f}%</b>")
    if ss.get("x_mentions") is not None:
        chips.append(f"X mentions / wk: <b>{ss['x_mentions']:,}</b>")
    if ss.get("reddit_mentions") is not None:
        chips.append(f"Reddit / wk: <b>{ss['reddit_mentions']:,}</b>")
    if chips:
        story.append(Paragraph("  ·  ".join(chips), styles["body"]))
    if ss.get("summary"):
        story.append(Paragraph(ss["summary"], styles["body_justified"]))
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 15. RECOMMENDATION TABLE
# ============================================================

def add_recommendation_table(story, narrative, styles):
    table_data = narrative.get("recommendation_table") or []
    if not table_data:
        return
    _section(story, "Recommendation", "Action by holder type", styles)
    rows = []
    for r in table_data:
        rows.append([
            Paragraph(f"<font color='{C_MUTED.hexval()}' size='7'>{r.get('action', '').upper()}</font>", styles["body"]),
            Paragraph(f"<b>{r.get('detail', '')}</b>", styles["body"]),
        ])
    t = Table(rows, colWidths=[4 * cm, 12.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), C_LIGHTER),
        ("BOX", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3 * cm))


# ============================================================
# 16. CATALYSTS TO WATCH
# ============================================================

def add_catalysts_to_watch(story, narrative, styles):
    catalysts = narrative.get("catalysts_to_watch") or []
    if not catalysts:
        return
    _section(story, "Catalysts to watch", "", styles)
    for c in catalysts:
        story.append(Paragraph(f"• {c}", styles["thesis_bullet"]))
    story.append(Spacer(1, 0.2 * cm))


# ============================================================
# 17. DATA GAPS
# ============================================================

def add_data_gaps(story, narrative, styles):
    gaps = narrative.get("data_gaps") or []
    if not gaps:
        return
    _section(story, "Data gaps", "What this cycle could not pull · to be filled next run", styles)
    for g in gaps:
        story.append(Paragraph(f"<font color='{C_MUTED.hexval()}'>• {g}</font>", styles["body"]))
    story.append(Spacer(1, 0.2 * cm))


# ============================================================
# Helpers
# ============================================================

def _section(story, title, subtitle, styles):
    """Section header: dark band with title + light subtitle."""
    box_data = [[Paragraph(f"<b>{title}</b>", styles["section"])]]
    if subtitle:
        box_data.append([Paragraph(subtitle, styles["section_subtitle"])])
    t = Table(box_data, colWidths=[16.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_LIGHTER),
        ("BOX", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, C_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.1 * cm))


def _key_callout(story, label, body, styles, color=C_MUTED, bg=C_LIGHTER):
    """A bordered callout box with an uppercase label and body text."""
    p = Paragraph(
        f"<font color='{color.hexval()}' size='7'><b>{label}</b></font><br/>"
        f"<font color='{C_TEXT.hexval()}'>{body}</font>",
        styles["body"]
    )
    t = Table([[p]], colWidths=[16.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)


def _callout_paragraph(text, styles, color=C_BAD, bg=C_BAD_BG):
    """Build a colored callout for the bear paragraph."""
    p = Paragraph(text, styles["body_justified"])
    t = Table([[p]], colWidths=[16.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.3, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _numbered_card(story, card, styles, color, bg):
    """A numbered catalyst/breaker card: small color chip with number + title and body."""
    num = card.get("id", "")
    title = card.get("title", "")
    body = card.get("body", "")
    head = Table(
        [[
            Paragraph(f"<font color='{color.hexval()}' size='12'><b>{num}</b></font>", styles["body"]),
            Paragraph(f"<b>{title}</b>", styles["body"]),
        ]],
        colWidths=[1.0 * cm, 15.5 * cm]
    )
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), bg),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(head)
    if body:
        story.append(Paragraph(body, styles["card_body"]))
    story.append(Spacer(1, 0.15 * cm))


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
        topMargin=1.9 * cm, bottomMargin=1.6 * cm,
        title=f"{narrative['ticker']} — {narrative['name']}",
        author="IntelliDesk Equity Research",
    )

    story = []
    add_header(story, narrative, valuation, styles)
    add_thesis(story, narrative, styles)
    add_key_numbers(story, narrative, styles)
    add_business_overview(story, narrative, styles)
    add_historical_financials(story, narrative, styles)
    add_industry_position(story, narrative, styles)
    add_bull_catalysts(story, narrative, styles)
    add_bear_breakers(story, narrative, styles)
    add_key_risks(story, narrative, styles)
    add_peers(story, narrative, styles)
    add_valuation_summary(story, narrative, valuation, styles)
    add_synthesis_paths(story, narrative, valuation, styles)
    add_management(story, narrative, styles)
    add_social_sentiment(story, narrative, styles)
    add_recommendation_table(story, narrative, styles)
    add_catalysts_to_watch(story, narrative, styles)
    add_data_gaps(story, narrative, styles)

    handler = _draw_header_footer(narrative["ticker"], narrative["name"])
    doc.build(story, onFirstPage=handler, onLaterPages=handler)
    print(f"[OK] wrote {output_path}")


if __name__ == "__main__":
    main()
