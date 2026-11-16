import streamlit as st

from auditor import ask_gemini, build_excel, build_prompt, run_audit

st.set_page_config(page_title="SEO Website Auditor", page_icon="🔍", layout="wide")


def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


# ---- optional password gate (set APP_PASSWORD in Streamlit secrets) ----
app_password = secret("APP_PASSWORD")
if app_password:
    if st.session_state.get("authed") is not True:
        st.title("🔍 SEO Website Auditor")
        pw = st.text_input("Password", type="password")
        if pw:
            if pw == app_password:
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Wrong password.")
        st.stop()

st.title("🔍 SEO Website Auditor")
st.caption("Enter a website, get a technical SEO audit and an AI-ready strategy brief. "
           "Only audit sites you own or have permission to audit.")

MAX_ALLOWED = 150

with st.form("audit_form"):
    url = st.text_input("Website URL", placeholder="https://www.example.com")
    c1, c2 = st.columns(2)
    business = c1.text_area("What does the business do?", height=90,
                            placeholder="e.g. Local dental clinic in Hyderabad offering implants and braces")
    goals = c2.text_area("Target audience and goals", height=90,
                         placeholder="e.g. Adults 25-50 in Hyderabad; goal: more appointment bookings")
    c3, c4 = st.columns(2)
    max_pages = c3.slider("Maximum pages to crawl", 10, MAX_ALLOWED, 50, step=10)
    use_psi = c4.checkbox("Include Google PageSpeed (slower)", value=True)
    submitted = st.form_submit_button("Run audit", type="primary")

if submitted:
    if not url.strip():
        st.warning("Please enter a website URL.")
    else:
        bar = st.progress(0.0, text="Starting...")
        try:
            res = run_audit(url, max_pages=max_pages, run_pagespeed=use_psi,
                            progress=lambda f, msg: bar.progress(f, text=msg))
            res["business"], res["goals"] = business, goals
            st.session_state["res"] = res
            st.session_state.pop("ai_text", None)
            bar.empty()
        except Exception as e:
            bar.empty()
            st.error(f"Could not complete the audit: {e}")

res = st.session_state.get("res")
if res:
    s = res["summary"]
    st.subheader(f"Results for {res['url']}")
    m = st.columns(5)
    m[0].metric("Pages crawled", s["Pages crawled"])
    m[1].metric("High issues", s["High-severity issues"])
    m[2].metric("Medium issues", s["Medium-severity issues"])
    m[3].metric("Low issues", s["Low-severity issues"])
    m[4].metric("Avg response (s)", s["Average response time (s)"])
    if s["Crawl hit page limit"] == "Yes":
        st.info("The crawl stopped at your page limit, so the site has more pages than were audited.")

    st.download_button("⬇️ Download Excel report", data=build_excel(res),
                       file_name=f"seo_audit_{res['domain']}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    t1, t2, t3, t4 = st.tabs(["Top issues", "All issues", "Pages", "AI strategy"])

    with t1:
        st.dataframe(res["top_issues"], hide_index=True)
        if any(k.startswith("PageSpeed") for k in s):
            st.markdown("**PageSpeed (mobile, homepage)**")
            st.write({k.replace("PageSpeed: ", ""): v for k, v in s.items() if k.startswith("PageSpeed")})

    with t2:
        f1, f2 = st.columns(2)
        sev = f1.multiselect("Severity", ["High", "Medium", "Low"], default=["High", "Medium"])
        cats = f2.multiselect("Category", sorted(res["issues"]["category"].unique()))
        df = res["issues"][res["issues"]["severity"].isin(sev)]
        if cats:
            df = df[df["category"].isin(cats)]
        st.caption(f"{len(df)} issues shown")
        st.dataframe(df, hide_index=True)

    with t3:
        cols = ["url", "status", "depth", "internal_inlinks", "title", "meta_description", "h1_text",
                "word_count", "response_time_s", "schema_types"]
        st.dataframe(res["pages"][cols], hide_index=True)

    with t4:
        prompt = build_prompt(res, res.get("business", ""), res.get("goals", ""))
        key = secret("GEMINI_API_KEY")
        if key:
            if st.button("✨ Generate SEO strategy with AI"):
                with st.spinner("Writing strategy..."):
                    try:
                        st.session_state["ai_text"] = ask_gemini(prompt, key, secret("GEMINI_MODEL", "gemini-2.5-flash"))
                    except Exception as e:
                        st.error(f"AI request failed: {e}")
            if st.session_state.get("ai_text"):
                st.markdown(st.session_state["ai_text"])
                st.download_button("⬇️ Download strategy (.md)", st.session_state["ai_text"],
                                   file_name=f"seo_strategy_{res['domain']}.md")
        else:
            st.info("No AI key is connected, so copy the brief below into Claude or any AI chat to get the strategy.")
        with st.expander("Strategy brief (copy this into Claude)", expanded=not key):
            st.code(prompt, language=None)
