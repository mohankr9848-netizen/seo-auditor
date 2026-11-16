"""SEO audit engine: crawl a site, find issues, build an Excel report and an AI prompt."""
import gzip
import io
import ipaddress
import json
import re
import socket
import time
from collections import Counter, defaultdict, deque
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MySEOAuditBot/1.0)"}
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".zip", ".mp4", ".mp3",
            ".css", ".js", ".xml", ".json", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".woff",
            ".woff2", ".avi", ".mov", ".rar", ".gz", ".txt")
SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}


# ---------- helpers ----------
def normalize(u):
    u, _ = urldefrag(u.strip())
    p = urlparse(u)
    return p._replace(path=p.path or "/", fragment="").geturl()


def clean_input_url(u):
    u = u.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return normalize(u)


def is_safe_url(u, allow_private=False):
    """Block localhost / private networks so the public app can't be used to probe internal systems."""
    if allow_private:
        return True
    try:
        host = urlparse(u).hostname
        if not host:
            return False
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def _collect_types(obj, out):
    if isinstance(obj, list):
        for i in obj:
            _collect_types(i, out)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        if isinstance(t, list):
            out.update(map(str, t))
        elif t:
            out.add(str(t))
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _collect_types(v, out)


# ---------- main audit ----------
def run_audit(start_url, max_pages=100, delay=0.3, respect_robots=True, external_limit=30,
              run_pagespeed=True, progress=None, allow_private=False):
    start_url = clean_input_url(start_url)
    if not is_safe_url(start_url, allow_private):
        raise ValueError("This address can't be audited (it is private, invalid, or unreachable).")

    session = requests.Session()
    session.headers.update(HEADERS)
    p0 = urlparse(start_url)
    base = f"{p0.scheme}://{p0.netloc}"
    domain = p0.netloc.lower().replace("www.", "")

    def same_site(u):
        return urlparse(u).netloc.lower().replace("www.", "") == domain

    # robots.txt
    robots_txt, robots_sitemaps = None, []
    rp = robotparser.RobotFileParser()
    try:
        rr = session.get(base + "/robots.txt", timeout=15)
        if rr.status_code == 200 and "html" not in rr.headers.get("content-type", "").lower():
            robots_txt = rr.text
            rp.parse(robots_txt.splitlines())
            robots_sitemaps = [l.split(":", 1)[1].strip() for l in robots_txt.splitlines()
                               if l.lower().startswith("sitemap:")]
    except Exception:
        pass

    def allowed(u):
        return True if (not respect_robots or robots_txt is None) else rp.can_fetch("*", u)

    # ---- crawl ----
    pages, inlinks, external_links = {}, defaultdict(set), defaultdict(set)
    blocked = set()
    queue, queued = deque([(start_url, 0)]), {start_url}
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        row = {"url": url, "depth": depth, "status": None, "final_status": None, "final_url": url,
               "redirect_hops": 0, "response_time_s": None, "is_html": False, "title": "", "title_len": 0,
               "meta_description": "", "meta_desc_len": 0, "h1_count": 0, "h1_text": "", "h2_count": 0,
               "canonical": "", "meta_robots": "", "x_robots_tag": "", "word_count": 0, "images": 0,
               "images_missing_alt": 0, "internal_links_out": 0, "external_links_out": 0,
               "viewport": False, "lang": "", "og_title": False, "schema_types": "", "html_kb": 0,
               "error": ""}
        try:
            r = session.get(url, timeout=20, allow_redirects=True)
            if not is_safe_url(r.url, allow_private):
                raise ValueError("redirected to a private address")
            row["response_time_s"] = round(r.elapsed.total_seconds(), 2)
            row["status"] = r.history[0].status_code if r.history else r.status_code
            row["final_status"] = r.status_code
            row["final_url"] = r.url
            row["redirect_hops"] = len(r.history)
            ctype = r.headers.get("content-type", "").lower()
            row["x_robots_tag"] = r.headers.get("X-Robots-Tag", "")
            row["html_kb"] = round(len(r.content) / 1024, 1)
        except Exception as e:
            row["status"], row["error"] = "ERROR", str(e)[:150]
            pages[url] = row
            continue
        pages[url] = row
        queued.add(normalize(r.url))

        if r.status_code == 200 and "text/html" in ctype and same_site(r.url):
            row["is_html"] = True
            soup = BeautifulSoup(r.text, "lxml")
            row["title"] = soup.title.get_text(strip=True) if soup.title else ""
            row["title_len"] = len(row["title"])
            md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
            row["meta_description"] = (md.get("content") or "").strip() if md else ""
            row["meta_desc_len"] = len(row["meta_description"])
            mr = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
            row["meta_robots"] = (mr.get("content") or "").lower() if mr else ""
            can = soup.find("link", rel="canonical")
            row["canonical"] = (can.get("href") or "").strip() if can else ""
            h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
            row["h1_count"], row["h1_text"] = len(h1s), (h1s[0] if h1s else "")
            row["h2_count"] = len(soup.find_all("h2"))
            row["viewport"] = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}) is not None
            row["lang"] = (soup.html.get("lang") or "") if soup.html else ""
            row["og_title"] = soup.find("meta", attrs={"property": "og:title"}) is not None
            imgs = soup.find_all("img")
            row["images"] = len(imgs)
            row["images_missing_alt"] = sum(1 for i in imgs if i.get("alt") is None)
            types = set()
            for s in soup.find_all("script", type="application/ld+json"):
                try:
                    _collect_types(json.loads(s.string or ""), types)
                except Exception:
                    pass
            row["schema_types"] = ", ".join(sorted(types))
            n_int = n_ext = 0
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "whatsapp:")):
                    continue
                try:
                    full = normalize(urljoin(r.url, href))
                except Exception:
                    continue
                if not full.startswith(("http://", "https://")):
                    continue
                if same_site(full):
                    n_int += 1
                    inlinks[full].add(url)
                    if (full not in queued and len(queued) < max_pages
                            and not urlparse(full).path.lower().endswith(SKIP_EXT)):
                        if allowed(full):
                            queued.add(full)
                            queue.append((full, depth + 1))
                        else:
                            blocked.add(full)
                else:
                    n_ext += 1
                    external_links[full].add(url)
            row["internal_links_out"], row["external_links_out"] = n_int, n_ext
            for tag in soup(["script", "style", "noscript", "template"]):
                tag.decompose()
            row["word_count"] = len(soup.get_text(" ", strip=True).split())
        if progress:
            progress(min(len(pages) / max_pages, 1.0), f"Crawled {len(pages)} pages...")
        time.sleep(delay)
    truncated = len(queue) > 0

    # ---- sitemap ----
    if progress:
        progress(1.0, "Checking sitemap, links and speed...")
    sitemap_urls, sitemap_ok, seen_maps = set(), [], set()
    stack = list(robots_sitemaps) or [base + "/sitemap.xml"]
    while stack and len(seen_maps) < 30:
        sm = stack.pop()
        if sm in seen_maps or not is_safe_url(sm, allow_private):
            continue
        seen_maps.add(sm)
        try:
            rr = session.get(sm, timeout=20)
            if rr.status_code != 200:
                continue
            content = gzip.decompress(rr.content) if sm.endswith(".gz") else rr.content
            xml = BeautifulSoup(content, "xml")
            if not (xml.find("urlset") or xml.find("sitemapindex")):
                continue
            sitemap_ok.append(sm)
            for s in xml.find_all("sitemap"):
                loc = s.find("loc")
                if loc:
                    stack.append(loc.text.strip())
            for t in xml.find_all("url"):
                loc = t.find("loc")
                if loc and len(sitemap_urls) < 20000:
                    sitemap_urls.add(normalize(loc.text.strip()))
        except Exception:
            pass

    # ---- external links ----
    ext_rows = []
    for eu in list(external_links.keys())[:external_limit]:
        if not is_safe_url(eu, allow_private):
            continue
        try:
            rr = session.head(eu, timeout=10, allow_redirects=True)
            if rr.status_code >= 400:
                rr = session.get(eu, timeout=10, allow_redirects=True, stream=True)
            code = rr.status_code
        except Exception:
            code = "ERROR"
        broken = code == "ERROR" or code in (404, 410) or (isinstance(code, int) and code >= 500)
        ext_rows.append({"external_url": eu, "status": code, "broken": broken,
                         "linked_from": ", ".join(list(external_links[eu])[:3])})
    ext_df = pd.DataFrame(ext_rows, columns=["external_url", "status", "broken", "linked_from"])

    # ---- PageSpeed ----
    psi = {}
    if run_pagespeed and not allow_private:
        try:
            resp = requests.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                                params=[("url", start_url), ("strategy", "mobile"), ("category", "performance"),
                                        ("category", "seo"), ("category", "accessibility"),
                                        ("category", "best-practices")], timeout=90)
            if resp.status_code == 200:
                lh = resp.json()["lighthouseResult"]
                psi = {k.title() + " score": round((v["score"] or 0) * 100) for k, v in lh["categories"].items()}
                for key, label in [("first-contentful-paint", "FCP"), ("largest-contentful-paint", "LCP"),
                                   ("cumulative-layout-shift", "CLS"), ("total-blocking-time", "TBT")]:
                    psi[label] = lh["audits"][key]["displayValue"]
        except Exception:
            pass

    # ---- issues ----
    issues = []

    def add(sev, cat, issue, url="", detail=""):
        issues.append({"severity": sev, "category": cat, "issue": issue, "url": url, "detail": detail})

    html_pages = [p for p in pages.values() if p["is_html"]]
    tcount = Counter(p["title"].strip().lower() for p in html_pages if p["title"].strip())
    dcount = Counter(p["meta_description"].strip().lower() for p in html_pages if p["meta_description"].strip())

    if robots_txt is None:
        add("Medium", "Crawlability", "robots.txt missing or unreachable", base + "/robots.txt")
    if not sitemap_ok:
        add("High", "Crawlability", "No XML sitemap found", base + "/sitemap.xml",
            "Create one and reference it in robots.txt and Google Search Console.")
    if urlparse(start_url).scheme != "https":
        add("High", "Security", "Site is not served over HTTPS", start_url)
    if blocked:
        add("Low", "Crawlability", "Internal URLs blocked by robots.txt (not crawled)", "",
            f"{len(blocked)} URLs, e.g. " + ", ".join(list(blocked)[:5]))

    for p in pages.values():
        u = p["url"]
        srcs = ", ".join(list(inlinks.get(u, []))[:3])
        if p["status"] == "ERROR":
            add("High", "Broken pages", "Page could not be loaded", u, p["error"] + (f" | Linked from: {srcs}" if srcs else ""))
            continue
        if isinstance(p["final_status"], int) and p["final_status"] >= 400:
            add("High", "Broken pages", "Page returns an error (4xx/5xx)", u,
                f"Status {p['final_status']}" + (f" | Linked from: {srcs}" if srcs else ""))
            continue
        if p["redirect_hops"] >= 2:
            add("Medium", "Redirects", "Redirect chain (2+ hops)", u, f"{p['redirect_hops']} hops -> {p['final_url']}")
        elif p["redirect_hops"] == 1 and u != start_url:
            add("Low", "Redirects", "Internal link points to a redirecting URL", u,
                f"-> {p['final_url']}" + (f" | Linked from: {srcs}" if srcs else ""))
        if not p["is_html"]:
            continue
        if p["response_time_s"] and p["response_time_s"] > 2:
            add("Medium", "Performance", "Slow server response (over 2s)", u, f"{p['response_time_s']}s")
        if not p["title"]:
            add("High", "On-page", "Missing title tag", u)
        else:
            if p["title_len"] > 60:
                add("Low", "On-page", "Title too long (over 60 chars)", u, f"{p['title_len']} chars: {p['title']}")
            elif p["title_len"] < 30:
                add("Low", "On-page", "Title too short (under 30 chars)", u, f"{p['title_len']} chars: {p['title']}")
            if tcount[p["title"].strip().lower()] > 1:
                add("Medium", "On-page", "Duplicate title tag", u, p["title"])
        if not p["meta_description"]:
            add("Medium", "On-page", "Missing meta description", u)
        else:
            if p["meta_desc_len"] > 160:
                add("Low", "On-page", "Meta description too long (over 160 chars)", u, f"{p['meta_desc_len']} chars")
            elif p["meta_desc_len"] < 70:
                add("Low", "On-page", "Meta description too short (under 70 chars)", u, f"{p['meta_desc_len']} chars")
            if dcount[p["meta_description"].strip().lower()] > 1:
                add("Medium", "On-page", "Duplicate meta description", u)
        if p["h1_count"] == 0:
            add("Medium", "On-page", "Missing H1", u)
        elif p["h1_count"] > 1:
            add("Low", "On-page", "Multiple H1 tags", u, f"{p['h1_count']} H1 tags")
        if p["word_count"] < 300:
            add("Medium", "Content", "Thin content (under 300 words)", u, f"{p['word_count']} words")
        if p["images_missing_alt"] > 0:
            add("Low", "Images", "Images missing alt text", u, f"{p['images_missing_alt']} of {p['images']} images")
        if "noindex" in p["meta_robots"] or "noindex" in p["x_robots_tag"].lower():
            inmap = normalize(u) in sitemap_urls
            add("High" if inmap else "Medium", "Indexability", "Page is set to noindex", u,
                "Also listed in the sitemap, which sends mixed signals." if inmap else "")
        if not p["canonical"]:
            add("Low", "Indexability", "Missing canonical tag", u)
        elif normalize(urljoin(u, p["canonical"])) not in (normalize(u), normalize(p["final_url"])):
            add("Medium", "Indexability", "Canonical points to a different URL", u, p["canonical"])
        if not p["viewport"]:
            add("High", "Mobile", "Missing viewport meta tag (not mobile-friendly)", u)
        if not p["lang"]:
            add("Low", "On-page", "Missing lang attribute on <html>", u)
        if not p["schema_types"]:
            add("Low", "Structured data", "No schema markup found", u)
        if not p["og_title"]:
            add("Low", "Social", "Missing Open Graph tags", u)
        if p["depth"] > 3:
            add("Low", "Architecture", "Page is more than 3 clicks from the homepage", u, f"{p['depth']} clicks")

    crawled = set(normalize(x) for x in pages) | set(normalize(p["final_url"]) for p in pages.values())
    if sitemap_urls and not truncated:
        for o in sorted(sitemap_urls - crawled)[:200]:
            add("Medium", "Architecture", "Orphan page: in sitemap but not linked from any crawled page", o)
    for p in pages.values():
        if normalize(p["url"]) in sitemap_urls and (p["status"] == "ERROR" or (isinstance(p["status"], int) and p["status"] >= 300)):
            add("Medium", "Sitemap", "Sitemap lists a URL that errors or redirects", p["url"], f"Status {p['status']}")
    if sitemap_urls:
        for p in html_pages:
            if (p["final_status"] == 200 and "noindex" not in p["meta_robots"]
                    and normalize(p["url"]) not in sitemap_urls and normalize(p["final_url"]) not in sitemap_urls):
                add("Low", "Sitemap", "Indexable page missing from sitemap", p["url"])
    for _, r_ in (ext_df[ext_df["broken"]].iterrows() if len(ext_df) else []):
        add("Medium", "Broken links", "Broken external link", r_["external_url"],
            f"Status {r_['status']} | Linked from: {r_['linked_from']}")

    issues_df = pd.DataFrame(issues, columns=["severity", "category", "issue", "url", "detail"])
    issues_df = issues_df.sort_values(by="severity", key=lambda s: s.map(SEV_ORDER)).reset_index(drop=True)
    pages_df = pd.DataFrame(pages.values())
    inl = {u: len(s) for u, s in inlinks.items()}
    pages_df.insert(2, "internal_inlinks", pages_df["url"].map(lambda u: inl.get(u, 0)))
    n_html = max(len(html_pages), 1)
    summary = {
        "Website": start_url,
        "Pages crawled": len(pages),
        "HTML pages analysed": len(html_pages),
        "Crawl hit page limit": "Yes" if truncated else "No",
        "High-severity issues": int((issues_df.severity == "High").sum()),
        "Medium-severity issues": int((issues_df.severity == "Medium").sum()),
        "Low-severity issues": int((issues_df.severity == "Low").sum()),
        "Average response time (s)": round(pages_df["response_time_s"].dropna().mean(), 2) if len(pages_df) else 0,
        "Pages with schema markup (%)": round(100 * sum(1 for p in html_pages if p["schema_types"]) / n_html),
        "Average word count": round(sum(p["word_count"] for p in html_pages) / n_html),
        "Sitemap URLs listed": len(sitemap_urls),
        "robots.txt found": "Yes" if robots_txt is not None else "No",
    }
    summary.update({f"PageSpeed: {k}": v for k, v in psi.items()})
    top = (issues_df.groupby(["severity", "issue"]).size().reset_index(name="pages")
           .assign(o=lambda d: d.severity.map(SEV_ORDER)).sort_values(["o", "pages"], ascending=[True, False])
           .drop(columns="o").reset_index(drop=True))
    return {"url": start_url, "domain": domain, "summary": summary, "issues": issues_df, "top_issues": top,
            "pages": pages_df, "external": ext_df, "sitemap_urls": sorted(sitemap_urls)}


# ---------- outputs ----------
PAGE_COLS = ["url", "depth", "internal_inlinks", "status", "final_status", "redirect_hops", "response_time_s",
             "title", "title_len", "meta_description", "meta_desc_len", "h1_text", "h1_count", "h2_count",
             "word_count", "canonical", "meta_robots", "images", "images_missing_alt", "internal_links_out",
             "external_links_out", "schema_types", "html_kb", "error"]


def build_excel(res):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(list(res["summary"].items()), columns=["Metric", "Value"]).to_excel(xw, sheet_name="Summary", index=False)
        res["issues"].to_excel(xw, sheet_name="Issues", index=False)
        res["pages"][PAGE_COLS].to_excel(xw, sheet_name="Pages", index=False)
        res["external"].to_excel(xw, sheet_name="External links", index=False)
        pd.DataFrame({"sitemap_url": res["sitemap_urls"]}).to_excel(xw, sheet_name="Sitemap URLs", index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                w = max((len(str(c.value)) if c.value is not None else 0) for c in col[:60])
                ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 10), 60)
    return buf.getvalue()


def build_prompt(res, business, goals):
    L = ["You are a senior SEO strategist. Using the crawl data below, write an SEO strategy for this business.\n",
         f"WEBSITE: {res['url']}", f"BUSINESS: {business or 'Not provided - infer from the page titles'}",
         f"AUDIENCE AND GOALS: {goals or 'Not provided'}\n", "AUDIT SUMMARY:"]
    L += [f"- {k}: {v}" for k, v in res["summary"].items()]
    L.append("\nMOST COMMON ISSUES (severity | issue | pages affected):")
    L += [f"- {r.severity} | {r.issue} | {r.pages}" for r in res["top_issues"].head(30).itertuples()]
    L.append("\nPAGE INVENTORY (url | title | H1 | words):")
    for r in res["pages"][res["pages"]["is_html"]].head(40).itertuples():
        L.append(f"- {r.url} | {str(r.title)[:70]} | {str(r.h1_text)[:60]} | {r.word_count}")
    L.append("""
PLEASE PROVIDE:
1. Executive summary of the site's SEO health (plain English).
2. Technical fixes in priority order (impact vs effort): quick wins, medium, long-term.
3. On-page improvements (titles, meta descriptions, headings, content depth) with examples for key pages.
4. Keyword themes and content gaps this business should target, with search intent for each.
5. Suggested site structure and internal linking improvements.
6. Schema markup and local/brand visibility recommendations if relevant.
7. A 30-60-90 day action plan.
8. KPIs to track in Google Search Console and Google Analytics.
Note: this data has no keyword volume or backlink information, so mark any volume or competition guesses as estimates.""")
    return "\n".join(L)


def ask_gemini(prompt, api_key, model="gemini-2.5-flash"):
    """Optional: free-tier Gemini call. Returns text or raises a readable error."""
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                      json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini returned {r.status_code}: {r.text[:300]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)
