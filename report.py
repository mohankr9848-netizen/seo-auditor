"""Branded PDF client report (reportlab)."""
import io
import re
from datetime import datetime
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from recs import RECS

SEV_COLORS = {"High": "#C0392B", "Medium": "#D68910", "Low": "#2E86C1"}
W, H = A4
CONTENT_W = W - 36 * mm


def _t(s):
    """Sanitise for the built-in PDF fonts (Western characters only) and escape markup."""
    s = "" if s is None else str(s)
    return escape(s.encode("cp1252", "replace").decode("cp1252"))


def _inline(s):
    s = _t(s).replace("`", "")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"(?<![\*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?![\*\w])", r"<i>\1</i>", s)


def _para(markup, style, bullet=None):
    try:
        return Paragraph(markup, style, bulletText=bullet)
    except Exception:
        return Paragraph(re.sub(r"<[^>]+>", "", markup), style, bulletText=bullet)


def _styles(color):
    base = dict(fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=colors.HexColor("#222222"))
    return {
        "body": ParagraphStyle("body", **base, spaceAfter=4),
        "small": ParagraphStyle("small", **{**base, "fontSize": 8, "leading": 10.5}, wordWrap="CJK"),
        "smallb": ParagraphStyle("smallb", **{**base, "fontSize": 8, "leading": 10.5, "fontName": "Helvetica-Bold"}, wordWrap="CJK"),
        "head": ParagraphStyle("head", **{**base, "fontName": "Helvetica-Bold", "fontSize": 8, "textColor": colors.white}),
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Helvetica-Bold", "fontSize": 17, "leading": 21,
                                      "textColor": color, "spaceBefore": 6, "spaceAfter": 8}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold", "fontSize": 12, "leading": 15,
                                      "textColor": color, "spaceBefore": 8, "spaceAfter": 4}),
        "h3": ParagraphStyle("h3", **{**base, "fontName": "Helvetica-Bold", "fontSize": 10.5, "spaceBefore": 6, "spaceAfter": 2}),
        "bullet": ParagraphStyle("bullet", **base, leftIndent=14, bulletIndent=3, spaceAfter=2),
        "cover": ParagraphStyle("cover", **{**base, "fontSize": 12, "leading": 18}),
        "score": ParagraphStyle("score", **{**base, "fontName": "Helvetica-Bold", "fontSize": 38, "leading": 42}),
    }


def _table(rows, widths, color, align_right_cols=()):
    t = Table(rows, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), color), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5D8DC")),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6F7")]),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for c in align_right_cols:
        style.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _md(text, st):
    out = []
    for line in str(text).splitlines():
        l = line.rstrip()
        if not l.strip():
            out.append(Spacer(1, 3))
            continue
        if set(l.strip()) <= set("|-: "):
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", l)
        if m:
            out.append(_para(_inline(m.group(2)), st["h2" if len(m.group(1)) <= 2 else "h3"]))
            continue
        b = re.match(r"^\s*[-*\u2022]\s+(.*)", l)
        if b:
            out.append(_para(_inline(b.group(1)), st["bullet"], "\u2022"))
            continue
        n = re.match(r"^\s*(\d+)[.)]\s+(.*)", l)
        if n:
            out.append(_para(_inline(n.group(2)), st["bullet"], n.group(1) + "."))
            continue
        if l.strip().startswith("|"):
            l = l.strip().strip("|").replace("|", "  |  ")
        out.append(_para(_inline(l), st["body"]))
    return out


def _grade(score):
    if score >= 80:
        return "Good", "#1E8449"
    if score >= 60:
        return "Fair - room to improve", "#D68910"
    return "Needs attention", "#C0392B"


def build_pdf(res, brand, ai_text=None, comparison=None, diff=None):
    brand = brand or {}
    color = colors.HexColor(brand.get("color") or "#1F4E79")
    name = brand.get("name") or "SEO Audit"
    client = brand.get("client") or ""
    logo = brand.get("logo")
    st = _styles(color)
    s = res["summary"]
    score = int(s["Health score"])
    grade, gcolor = _grade(score)
    date_txt = datetime.now().strftime("%d %B %Y")

    def cover(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(color)
        canvas.rect(0, H - 85 * mm, W, 85 * mm, stroke=0, fill=1)
        top = H - 24 * mm
        if logo:
            try:
                ir = ImageReader(io.BytesIO(logo))
                iw, ih = ir.getSize()
                hh = 16 * mm
                ww = min(hh * iw / ih, 60 * mm)
                canvas.setFillColor(colors.white)
                canvas.roundRect(18 * mm - 3 * mm, top - 3 * mm, ww + 6 * mm, hh + 6 * mm, 3, stroke=0, fill=1)
                canvas.drawImage(ir, 18 * mm, top, ww, hh, mask="auto")
            except Exception:
                pass
        else:
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 15)
            canvas.drawString(18 * mm, top + 4 * mm, _t(name)[:60])
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 30)
        canvas.drawString(18 * mm, H - 58 * mm, "SEO Audit Report")
        canvas.setFont("Helvetica", 14)
        canvas.drawString(18 * mm, H - 69 * mm, _t(res["domain"])[:70])
        canvas.restoreState()

    def later(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(color)
        canvas.setLineWidth(1.2)
        canvas.line(18 * mm, H - 14 * mm, W - 18 * mm, H - 14 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(18 * mm, H - 11 * mm, _t(name)[:60])
        canvas.drawRightString(W - 18 * mm, H - 11 * mm, "SEO Audit - " + _t(res["domain"])[:50])
        canvas.drawString(18 * mm, 10 * mm, f"Prepared {date_txt}")
        canvas.drawRightString(W - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    story = [Spacer(1, 78 * mm)]
    if client:
        story.append(_para(f"<b>Prepared for:</b> {_t(client)}", st["cover"]))
    story += [_para(f"<b>Prepared by:</b> {_t(name)}", st["cover"]), _para(f"<b>Date:</b> {date_txt}", st["cover"]),
              _para(f"<b>Website audited:</b> {_t(res['url'])}", st["cover"]), Spacer(1, 14 * mm),
              _para("Overall SEO health", st["h3"]),
              _para(f'<font color="{gcolor}">{score}/100</font>', st["score"]),
              _para(f'<font color="{gcolor}"><b>{_t(grade)}</b></font>', st["cover"]), PageBreak()]

    # ---- executive summary ----
    hi, me, lo = s["High-severity issues"], s["Medium-severity issues"], s["Low-severity issues"]
    story.append(_para("Executive summary", st["h1"]))
    story.append(_para(
        f"We crawled <b>{s['Pages crawled']}</b> pages of <b>{_t(res['domain'])}</b> and found <b>{hi}</b> high-priority, "
        f"<b>{me}</b> medium-priority and <b>{lo}</b> low-priority issues. The overall health score is <b>{score}/100</b>. "
        "High-priority items can stop pages from being found or used and should be fixed first.", st["body"]))
    if s["Crawl hit page limit"] == "Yes":
        story.append(_para("<i>Note: the audit reached its page limit, so the site contains more pages than were analysed.</i>", st["body"]))
    top = res["top_issues"]
    if len(top):
        worst = top.head(3)
        story.append(_para("Biggest problems found: " + "; ".join(
            f"<b>{_t(r.issue)}</b> ({r.pages} page{'s' if r.pages != 1 else ''})" for r in worst.itertuples()) + ".", st["body"]))
    story.append(Spacer(1, 4))
    keys = ["Pages crawled", "HTML pages analysed", "Average response time (s)", "Average word count",
            "Pages with schema markup (%)", "Sitemap URLs listed", "robots.txt found"]
    rows = [[_para("Metric", st["head"]), _para("Result", st["head"])]]
    rows += [[_para(_t(k), st["small"]), _para(_t(s[k]), st["smallb"])] for k in keys if k in s]
    rows += [[_para("PageSpeed (mobile): " + _t(k.replace("PageSpeed: ", "")), st["small"]), _para(_t(v), st["smallb"])]
             for k, v in s.items() if k.startswith("PageSpeed")]
    story += [_para("Key metrics", st["h2"]), _table(rows, [CONTENT_W * 0.6, CONTENT_W * 0.4], color)]

    # ---- changes since last audit ----
    if diff:
        d = diff["diff"]
        story += [PageBreak(), _para("Progress since the previous audit", st["h1"])]
        old_d = _t(diff.get("old_date", "earlier audit"))
        rows = [[_para(x, st["head"]) for x in ("Metric", f"Previous ({old_d})", "Now")]]
        for k, (a, b) in d["deltas"].items():
            rows.append([_para(_t(k), st["small"]), _para(_t(a), st["small"]), _para(_t(b), st["smallb"])])
        story.append(_table(rows, [CONTENT_W * 0.5, CONTENT_W * 0.3, CONTENT_W * 0.2], color))
        story.append(Spacer(1, 6))
        story.append(_para(f"<b>{len(d['fixed'])}</b> issues were resolved and <b>{len(d['new'])}</b> new issues appeared.", st["body"]))
        if not d["same_size"]:
            story.append(_para("<i>The two audits crawled different numbers of pages, so some differences may come from crawl size.</i>", st["body"]))
        for title, df in (("Most-fixed issue types", d["fixed"]), ("New issue types to address", d["new"])):
            if len(df):
                g = df.groupby("issue").size().sort_values(ascending=False).head(8)
                story.append(_para(title, st["h2"]))
                story += [_para(f"{_t(i)}: {n}", st["bullet"], "\u2022") for i, n in g.items()]

    # ---- priority issues ----
    story += [PageBreak(), _para("Priority issues and how to fix them", st["h1"])]
    rows = [[_para(x, st["head"]) for x in ("Priority", "Issue", "Pages", "Recommended fix")]]
    sev_styles = []
    for i, r in enumerate(top.head(30).itertuples(), start=1):
        rows.append([_para(f'<font color="{SEV_COLORS[r.severity]}"><b>{r.severity}</b></font>', st["small"]),
                     _para(_t(r.issue), st["smallb"]), _para(str(r.pages), st["small"]),
                     _para(_t(RECS.get(r.issue, "")), st["small"])])
    story.append(_table(rows, [CONTENT_W * 0.12, CONTENT_W * 0.28, CONTENT_W * 0.08, CONTENT_W * 0.52], color))

    high = top[top["severity"] == "High"].head(8)
    if len(high):
        story += [Spacer(1, 6), _para("Examples of high-priority problems", st["h1"])]
        for r in high.itertuples():
            ex = res["issues"][res["issues"]["issue"] == r.issue].head(3)
            story.append(_para(f"{_t(r.issue)} ({r.pages})", st["h3"]))
            for e in ex.itertuples():
                detail = f" - {_t(e.detail)[:120]}" if e.detail else ""
                story.append(_para(f"{_t(e.url)}{detail}", st["bullet"], "\u2022"))

    # ---- comparison ----
    if comparison is not None and len(comparison["table"]):
        tbl = comparison["table"]
        story += [PageBreak(), _para("Competitor comparison", st["h1"])]
        n = len(tbl.columns) - 1
        first = CONTENT_W * 0.30
        widths = [first] + [(CONTENT_W - first) / n] * n
        rows = [[_para(_t(c), st["head"]) for c in tbl.columns]]
        rows += [[_para(_t(v), st["smallb" if j == 0 else "small"]) for j, v in enumerate(r)] for r in tbl.itertuples(index=False)]
        story.append(_table(rows, widths, color))
        gap = comparison.get("gap")
        if gap is not None and len(gap):
            story += [Spacer(1, 8), _para("Topic opportunities", st["h2"]),
                      _para("Words that competitors use across several page titles and headings, but this site does not use at all. "
                            "Review them for content ideas.", st["body"])]
            story.append(_para(", ".join(f"{_t(r['Topic word'])} ({r['Competitor pages using it']})"
                                         for _, r in gap.head(30).iterrows()), st["body"]))

    # ---- strategy ----
    if ai_text:
        story += [PageBreak(), _para("SEO strategy and recommendations", st["h1"])] + _md(ai_text, st)

    # ---- notes ----
    story += [Spacer(1, 10), _para("About this report", st["h2"]),
              _para("This audit is based on an automated crawl of publicly accessible pages. It does not include backlink, keyword "
                    "ranking or traffic data. Sites that rely heavily on JavaScript may be under-reported. The health score is an "
                    "indicative measure for tracking progress over time, not an official search engine metric.", st["body"])]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=22 * mm,
                            bottomMargin=18 * mm, title=f"SEO Audit - {res['domain']}", author=name)
    doc.build(story, onFirstPage=cover, onLaterPages=later)
    return buf.getvalue()
