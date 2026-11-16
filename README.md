# SEO Website Auditor

Crawl up to 500 pages, find technical SEO issues, track progress over time, compare competitors,
and export a branded PDF report.

Files: `app.py` (web interface), `auditor.py` (crawler), `storage.py` (history), `compare.py` (competitors),
`report.py` (PDF), `recs.py` (fix advice), `requirements.txt`. Optional: `logo.png` (used on PDF covers).

## Optional secrets (Streamlit app -> Settings -> Secrets)
```
APP_PASSWORD = "choose-a-password"
GEMINI_API_KEY = "key-from-aistudio.google.com"
BRAND_NAME = "Your Agency Name"
BRAND_COLOR = "#1F4E79"
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "your-supabase-secret-key"
```

## Permanent history (free Supabase)
1. Create a project at supabase.com.
2. Open SQL Editor, paste the SQL below and click Run.
3. Project Settings -> API: copy the Project URL and the secret (service_role) key into the secrets above.

```sql
create table if not exists audits (
  id text primary key,
  created_at timestamptz default now(),
  domain text, url text, kind text, group_id text,
  health int, pages int, high int, medium int, low int,
  payload text
);
alter table audits enable row level security;
```
