"""Saved audit history. Uses Supabase (free, permanent) if configured, otherwise a local SQLite file
(works immediately but is wiped whenever the Streamlit app restarts)."""
import base64
import gzip
import json
import sqlite3
import uuid
from datetime import datetime, timezone

import pandas as pd
import requests

META_COLS = ["id", "created_at", "domain", "url", "kind", "group_id", "health", "pages", "high", "medium", "low"]
DF_KEYS = ("issues", "top_issues", "pages", "external")

SUPABASE_SQL = """create table if not exists audits (
  id text primary key,
  created_at timestamptz default now(),
  domain text, url text, kind text, group_id text,
  health int, pages int, high int, medium int, low int,
  payload text
);
alter table audits enable row level security;"""


def _default(o):
    return o.item() if hasattr(o, "item") else str(o)


def pack(res):
    d = {k: v for k, v in res.items() if k not in DF_KEYS}
    for k in DF_KEYS:
        d[k] = json.loads(res[k].to_json(orient="split", default_handler=str))
    raw = json.dumps(d, default=_default).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def unpack(payload):
    d = json.loads(gzip.decompress(base64.b64decode(payload)))
    for k in DF_KEYS:
        x = d[k]
        d[k] = pd.DataFrame(x["data"], columns=x["columns"])
    return d


def meta_of(res, kind, group_id):
    s = res["summary"]
    return {"id": res["id"], "created_at": res["created_at"], "domain": res["domain"], "url": res["url"],
            "kind": kind, "group_id": group_id, "health": int(s["Health score"]), "pages": int(s["Pages crawled"]),
            "high": int(s["High-severity issues"]), "medium": int(s["Medium-severity issues"]),
            "low": int(s["Low-severity issues"])}


def save_audit(store, res, kind="main", group_id=None):
    res.setdefault("id", str(uuid.uuid4()))
    res.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    res["kind"], res["group_id"] = kind, group_id
    store.save(meta_of(res, kind, group_id), pack(res))
    return res["id"]


def update_audit(store, res):
    store.update(meta_of(res, res.get("kind", "main"), res.get("group_id")), pack(res))


class SQLiteStore:
    persistent = False
    name = "Temporary storage (resets when the app restarts)"

    def __init__(self, path="audits.db"):
        self.path = path
        with self._c() as c:
            c.execute("""create table if not exists audits (id text primary key, created_at text, domain text,
                         url text, kind text, group_id text, health int, pages int, high int, medium int,
                         low int, payload text)""")

    def _c(self):
        return sqlite3.connect(self.path)

    def save(self, meta, payload):
        with self._c() as c:
            c.execute("insert or replace into audits values (?,?,?,?,?,?,?,?,?,?,?,?)",
                      [meta[k] for k in META_COLS] + [payload])

    update = lambda self, meta, payload: self.save(meta, payload)  # noqa: E731

    def list(self, domain=None, limit=300):
        q, args = f"select {','.join(META_COLS)} from audits", []
        if domain:
            q += " where domain=?"
            args.append(domain)
        q += " order by created_at desc limit ?"
        args.append(limit)
        with self._c() as c:
            return pd.DataFrame(c.execute(q, args).fetchall(), columns=META_COLS)

    def get(self, audit_id):
        with self._c() as c:
            row = c.execute("select payload from audits where id=?", [audit_id]).fetchone()
        return row[0] if row else None

    def delete(self, audit_id):
        with self._c() as c:
            c.execute("delete from audits where id=?", [audit_id])


class SupabaseStore:
    persistent = True
    name = "Supabase (permanent)"

    def __init__(self, url, key):
        self.api = url.rstrip("/") + "/rest/v1/audits"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @staticmethod
    def _ok(r):
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase error {r.status_code}: {r.text[:200]}")
        return r

    def save(self, meta, payload):
        self._ok(requests.post(self.api, headers={**self.h, "Prefer": "return=minimal"},
                               json={**meta, "payload": payload}, timeout=60))

    def update(self, meta, payload):
        self._ok(requests.patch(self.api, params={"id": f"eq.{meta['id']}"}, headers=self.h,
                                json={**meta, "payload": payload}, timeout=60))

    def list(self, domain=None, limit=300):
        params = {"select": ",".join(META_COLS), "order": "created_at.desc", "limit": limit}
        if domain:
            params["domain"] = f"eq.{domain}"
        rows = self._ok(requests.get(self.api, params=params, headers=self.h, timeout=30)).json()
        return pd.DataFrame(rows, columns=META_COLS)

    def get(self, audit_id):
        rows = self._ok(requests.get(self.api, params={"id": f"eq.{audit_id}", "select": "payload"},
                                     headers=self.h, timeout=60)).json()
        return rows[0]["payload"] if rows else None

    def delete(self, audit_id):
        self._ok(requests.delete(self.api, params={"id": f"eq.{audit_id}"}, headers=self.h, timeout=30))


def make_store(supabase_url="", supabase_key=""):
    if supabase_url and supabase_key:
        return SupabaseStore(supabase_url, supabase_key)
    return SQLiteStore()
