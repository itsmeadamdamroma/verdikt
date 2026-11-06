#!/usr/bin/env python3
"""Verdikt DB — chiavi API, piani, quota giornaliera (SQLite)."""
import hashlib
import os
import secrets
import sqlite3
import time

DB_PATH = os.environ.get("VERDIKT_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "verdikt.db")

PLANS = {"free": 100, "pro": 5000, "owner": 1000000}


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS keys(
        key TEXT PRIMARY KEY, plan TEXT, email TEXT, created REAL, active INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS usage(
        key TEXT, day TEXT, checks INTEGER, PRIMARY KEY(key, day))""")
    c.execute("""CREATE TABLE IF NOT EXISTS checks_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT, ts REAL, claim TEXT, verdict TEXT,
        confidence REAL, ms INTEGER, sources INTEGER)""")
    return c


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def gen_key(plan="free", email=""):
    raw = "vk_" + secrets.token_hex(16)
    h = _hash(raw)
    c = _conn()
    c.execute("INSERT INTO keys VALUES(?,?,?,?,1)", (h, plan, email, time.time()))
    c.commit()
    c.close()
    return raw


def make_pro(raw_key):
    c = _conn()
    c.execute("UPDATE keys SET plan='pro' WHERE key=?", (_hash(raw_key),))
    c.commit()
    c.close()


def upgrade_by_hash(key_hash):
    """Upgrade diretto su hash SHA256 (usato dal webhook Stripe)."""
    c = _conn()
    c.execute("UPDATE keys SET plan='pro' WHERE key=?", (key_hash,))
    c.commit()
    c.close()


def verify(raw):
    h = _hash(raw)
    c = _conn()
    r = c.execute("SELECT plan, active FROM keys WHERE key=?", (h,)).fetchone()
    c.close()
    if r and r[1]:
        return r[0]
    return None


def check_quota(raw, limit):
    h = _hash(raw)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    c = _conn()
    row = c.execute("SELECT checks FROM usage WHERE key=? AND day=?", (h, day)).fetchone()
    used = row[0] if row else 0
    if used >= limit:
        c.close()
        return used, False
    c.execute(
        "INSERT INTO usage VALUES(?,?,1) ON CONFLICT(key,day) DO UPDATE SET checks=checks+1",
        (h, day))
    c.commit()
    c.close()
    return used + 1, True


def usage_stats(raw):
    h = _hash(raw)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    c = _conn()
    row = c.execute("SELECT checks FROM usage WHERE key=? AND day=?", (h, day)).fetchone()
    k = c.execute("SELECT plan FROM keys WHERE key=?", (h,)).fetchone()
    c.close()
    return {"plan": k[0] if k else None, "used_today": row[0] if row else 0}


def log_check(raw, claim, verdict, confidence, ms, sources):
    c = _conn()
    c.execute("INSERT INTO checks_log(key,ts,claim,verdict,confidence,ms,sources) "
              "VALUES(?,?,?,?,?,?,?)",
              (_hash(raw), time.time(), claim[:400], verdict, confidence, ms, sources))
    c.commit()
    c.close()


def recent_checks(raw, limit=50):
    c = _conn()
    rows = c.execute(
        "SELECT ts,claim,verdict,confidence,ms,sources FROM checks_log "
        "WHERE key=? ORDER BY id DESC LIMIT ?", (_hash(raw), limit)).fetchall()
    c.close()
    return [{"ts": r[0], "claim": r[1], "verdict": r[2], "confidence": r[3],
             "ms": r[4], "sources": r[5]} for r in rows]


def log_verdict(verdict):
    c = _conn()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    c.execute("""CREATE TABLE IF NOT EXISTS verdicts(
        day TEXT, verdict TEXT, n INTEGER, PRIMARY KEY(day, verdict))""")
    c.execute("INSERT INTO verdicts VALUES(?,?,1) ON CONFLICT(day,verdict) "
              "DO UPDATE SET n=n+1", (day, verdict))
    c.commit()
    c.close()


def dashboard_stats():
    c = _conn()
    tot = c.execute("SELECT COALESCE(SUM(checks),0) FROM usage").fetchone()[0]
    active = c.execute("SELECT COUNT(*) FROM keys WHERE active=1").fetchone()[0]
    pro = c.execute("SELECT COUNT(*) FROM keys WHERE active=1 AND plan='pro'"
                    ).fetchone()[0]
    try:
        c.execute("SELECT 1 FROM verdicts LIMIT 1")
        dist = dict(c.execute("SELECT verdict, SUM(n) FROM verdicts "
                              "GROUP BY verdict").fetchall())
    except sqlite3.OperationalError:
        dist = {}
    c.close()
    return {"total_checks": tot, "active_keys": active, "pro_keys": pro,
            "verdicts": dist}


if __name__ == "__main__":
    k = gen_key("free", "test@verdikt.ai")
    print("test key:", k, "plan:", verify(k))
