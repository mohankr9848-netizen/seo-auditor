"""Competitor comparison, topic gap and audit-vs-audit change tracking."""
import re
from collections import Counter, defaultdict

import pandas as pd

STOP = set("""this that with from your have more about home page pages contact best services service welcome learn what when
where which their there these those will into over than also been were them then they some such most only other very just
like make made many much need want here how why who our you are for and the can not all any use using get new top free
online official site website blog news read view shop store products product company business team help support
privacy policy terms conditions login sign cart checkout account""".split())


def _num(s):
    return pd.to_numeric(s, errors="coerce").fillna(0)


def site_metrics(res):
    df = res["pages"]
    h = df[df["is_html"] == True]  # noqa: E712
    n = max(len(h), 1)
    s = res["summary"]
    words, h1, links = _num(h["word_count"]), _num(h["h1_count"]), _num(h["internal_links_out"])

    def pct(mask):
        return f"{round(100 * int(mask.sum()) / n)}%"

    types = Counter(t.strip() for v in h["schema_types"].fillna("") for t in str(v).split(",") if t.strip())
    return {
        "Health score (0-100)": s["Health score"],
        "Pages crawled": s["Pages crawled"],
        "Issues per page": round(len(res["issues"]) / n, 1),
        "High-severity issues": s["High-severity issues"],
        "Average word count": round(float(words.mean())) if len(h) else 0,
        "Thin pages (<300 words)": pct(words < 300),
        "Pages missing title": pct(h["title"].fillna("") == ""),
        "Pages missing meta description": pct(h["meta_description"].fillna("") == ""),
        "Pages missing H1": pct(h1 == 0),
        "Pages with schema markup": pct(h["schema_types"].fillna("") != ""),
        "Top schema types": ", ".join(t for t, _ in types.most_common(4)) or "none",
        "Avg internal links per page": round(float(links.mean()), 1) if len(h) else 0,
        "Avg response time (s)": s["Average response time (s)"],
        "URLs in sitemap": s["Sitemap URLs listed"],
        "PageSpeed performance (mobile)": s.get("PageSpeed: Performance score", "n/a"),
    }


def comparison_table(named):
    """named: {label: audit result}. Returns a metrics x sites table (all text, safe to display)."""
    cols = {label: site_metrics(r) for label, r in named.items()}
    return pd.DataFrame(cols).astype(str).rename_axis("Metric").reset_index()


def _terms(res, drop):
    c = Counter()
    h = res["pages"][res["pages"]["is_html"] == True]  # noqa: E712
    for t, h1 in zip(h["title"].fillna(""), h["h1_text"].fillna("")):
        toks = {w for w in re.findall(r"[a-z][a-z\-]{3,}", f"{t} {h1}".lower()) if w not in STOP and w not in drop}
        c.update(toks)
    return c


def topic_gap(main, competitors, min_pages=2, top=40):
    """Words competitors use in titles/headings on several pages that your site never uses."""
    drop = set()
    for r in [main] + list(competitors):
        drop.update(re.split(r"[.\-]", r["domain"]))
    mine = _terms(main, drop)
    total, who = Counter(), defaultdict(set)
    for r in competitors:
        for term, n in _terms(r, drop).items():
            total[term] += n
            who[term].add(r["domain"])
    rows = [{"Topic word": t, "Competitor pages using it": n, "Used by": ", ".join(sorted(who[t]))}
            for t, n in total.most_common() if n >= min_pages and mine.get(t, 0) == 0]
    return pd.DataFrame(rows[:top], columns=["Topic word", "Competitor pages using it", "Used by"])


def diff_audits(old, new):
    """Issues fixed and newly introduced between two audits of the same site."""
    ko = set(zip(old["issues"]["issue"], old["issues"]["url"]))
    kn = set(zip(new["issues"]["issue"], new["issues"]["url"]))
    new_df = new["issues"][[(i, u) not in ko for i, u in zip(new["issues"]["issue"], new["issues"]["url"])]]
    fixed_df = old["issues"][[(i, u) not in kn for i, u in zip(old["issues"]["issue"], old["issues"]["url"])]]
    so, sn = old["summary"], new["summary"]
    deltas = {k: (so[k], sn[k]) for k in ["Health score", "Pages crawled", "High-severity issues",
                                          "Medium-severity issues", "Low-severity issues"]}
    return {"new": new_df.reset_index(drop=True), "fixed": fixed_df.reset_index(drop=True), "deltas": deltas,
            "same_size": so["Pages crawled"] == sn["Pages crawled"]}


def build_competitor_prompt(main, competitors, table, gap, business, goals):
    L = ["You are a senior SEO strategist. Compare my website against its competitors using the crawl data below "
         "and recommend how to outperform them.\n",
         f"MY WEBSITE: {main['url']}", f"BUSINESS: {business or 'Not provided - infer from page titles'}",
         f"AUDIENCE AND GOALS: {goals or 'Not provided'}",
         "COMPETITORS: " + ", ".join(r["url"] for r in competitors), "\nCOMPARISON TABLE:", table.to_string(index=False)]
    L.append("\nTOPIC WORDS COMPETITORS USE IN TITLES/HEADINGS BUT MY SITE DOES NOT:")
    L.append(", ".join(f"{r['Topic word']} ({r['Competitor pages using it']})" for _, r in gap.head(30).iterrows()) or "none found")
    L.append("\nMY TOP ISSUES (severity | issue | pages):")
    L += [f"- {r.severity} | {r.issue} | {r.pages}" for r in main["top_issues"].head(12).itertuples()]
    for r in [main] + list(competitors):
        L.append(f"\nPAGE TITLES - {r['domain']}:")
        h = r["pages"][r["pages"]["is_html"] == True].head(20)  # noqa: E712
        L += [f"- {str(t)[:80]}" for t in h["title"]]
    L.append("""
PLEASE PROVIDE:
1. Summary of how my site compares (strengths, weaknesses, where each competitor is ahead).
2. Technical and on-page gaps to close first (quick wins vs long-term).
3. Content and topic opportunities based on the topic-word gaps and competitor page titles, with search intent.
4. Page types or sections competitors have that I lack.
5. A 90-day plan to overtake the strongest competitor.
Note: there is no traffic, keyword-volume or backlink data here, so label any such claims as estimates.""")
    return "\n".join(L)
