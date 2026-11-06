#!/usr/bin/env python3
"""FactChecker Live — CashClaw. Pipeline 100% free-tier:
claims -> evidenze (DuckDuckGo) -> verdetto (Cloudflare Workers AI).
Uso:
    python3 engine.py "testo da verificare"
    python3 engine.py --demo          (3 claim di test)
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

from ddgs import DDGS
from . import clef_engine

MODELS_JSON = os.environ.get("CF_MODELS_JSON", "/root/.openclaw/agents/main/agent/models.json")
EXTRACT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8"
VERDICT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
MAX_EVIDENCE = 5
VERDICTS = {"true", "false", "misleading", "unverifiable"}
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "evidence_cache")
CACHE_TTL = 24 * 3600  # ponytail: cache per query, non semantica — stesso claim riformulato = doppia ricerca

_STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
         "is", "are", "was", "were", "be", "been", "its", "it's", "about",
         "with", "from", "by", "that", "this", "there", "these", "those",
         "which", "who", "than", "then", "also", "only", "every",
         "il", "lo", "la", "i", "gli", "le", "un", "una", "di", "a", "in",
         "da", "per", "con", "che", "è", "sono", "era", "erano", "circa",
         "più", "meno", "tra", "fra", "non", "anche", "come", "ha",
         "hanno", "aveva", "percento", "percent", "years", "anni", "years'"}


def _keywords(claim):
    """Query keyword: content words + numeri, senza stopword."""
    words = re.findall(r"[\w'\-\.]+", claim.lower())
    keep = [w for w in words if w not in _STOP and len(w) > 1]
    return " ".join(keep[:14]) or claim


def _ddg(query, max_results=MAX_EVIDENCE):
    try:
        return DDGS().text(query, max_results=max_results) or []
    except Exception:
        time.sleep(2)
        try:  # retry una volta su rate-limit/transitorio
            return DDGS().text(query, max_results=max_results) or []
        except Exception:
            return []


def _cache_get(query):
    path = os.path.join(CACHE_DIR,
                        hashlib.sha1(query.encode()).hexdigest() + ".json")
    try:
        if os.path.exists(path):
            d = json.load(open(path))
            if time.time() - d["ts"] < CACHE_TTL:
                return d["rows"]
    except Exception:
        pass
    return None


def _cache_set(query, rows):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR,
                        hashlib.sha1(query.encode()).hexdigest() + ".json")
    with open(path, "w") as f:
        json.dump({"ts": time.time(), "rows": rows}, f)


def _cf_creds():
    """Credenziali Cloudflare: env prima (CF_ACCOUNT_ID + CF_API_TOKEN),
    poi il models.json di OpenClaw come comodita' in locale."""
    token = os.environ.get("CF_API_TOKEN")
    acct = os.environ.get("CF_ACCOUNT_ID")
    if token and acct:
        return f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1", token
    d = json.load(open(MODELS_JSON))
    cf = d["providers"]["cloudflare"]
    return cf["baseUrl"].rstrip("/"), cf["apiKey"]


def _cf_chat(model, messages, max_tokens=500):
    base, key = _cf_creds()
    url = base + "/chat/completions"
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": max_tokens, "temperature": 0.1}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def _parse_json(text):
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        text = m.group()
    return json.loads(text)



def extract_claims(text, max_claims=5):
    """Estrae claims verificabili dal testo (JSON array di stringhe)."""
    prompt = (
        "Estrai dal testo seguente le affermazioni fattuali verificabili "
        f"(massimo {max_claims}). Escludi opinioni, promesse, previsioni. "
        "Riporta ogni affermazione TESTUALMENTE com'è scritta, senza "
        "correggerla o riformularla.\n"
        'Rispondi SOLO con JSON: {"claims": ["...", "..."]}\n\nTesto:\n' + text
    )
    out = _cf_chat(EXTRACT_MODEL, [{"role": "user", "content": prompt}], 400)
    try:
        return _parse_json(out).get("claims", [])[:max_claims]
    except Exception:
        return []


def _translate_en(text):
    """Traduce il claim in inglese (per fallback ricerca)."""
    prompt = ("Translate to English. Reply ONLY with the translation, "
              "nothing else:\n\n" + text)
    try:
        out = _cf_chat(EXTRACT_MODEL, [{"role": "user", "content": prompt}], 200)
        return out.strip().strip('"\'')
    except Exception:
        return None


def search_evidence(claim, max_results=MAX_EVIDENCE, fallback_en=True):
    """Cerca evidenze web via DuckDuckGo (free).
    Ladder query: claim -> keywords -> prima clausola -> traduzione EN.
    Cache su disco TTL 24h. Senza region fissa: DDG rileva la lingua dalla query.
    """
    queries = []
    if len(claim) > 60:
        kw = _keywords(claim)
        if kw and kw.lower() != claim.lower():
            queries.append(kw)
    first_clause = re.split(r"[.;,]", claim, 1)[0].strip()
    if first_clause and first_clause.lower() != claim.lower():
        queries.append(first_clause)
    rows = []
    for q in [claim] + queries:
        cached = _cache_get(q)
        if cached:  # ponytail: [] = miss — il cache vuoto non blocca le query successive
            rows = cached
        else:
            rows = _ddg(q, max_results)
            if rows:  # ponytail: cachea solo risultati non vuoti — un fail transitorio DDG non deve avvelenare la cache 24h
                _cache_set(q, rows)
        if rows:
            break
    if not rows and fallback_en and re.search(r"[àèéìòùÀÈÉÌÒÙ]", claim):
        en = _translate_en(claim)
        if en and en.lower() != claim.lower():
            cached = _cache_get(en)
            if cached:
                rows = cached
            else:
                rows = _ddg(en, max_results)
                if rows:
                    _cache_set(en, rows)
    ev = []
    for r in rows[:max_results]:
        ev.append({"title": (r.get("title") or "")[:120],
                   "url": r.get("href") or r.get("url") or "",
                   "snippet": (r.get("body") or "")[:400]})
    return ev


def verdict(claim, evidence, engine="clef"):
    """Verdetto su evidenze. engine='clef' (default, calibrato) o 'llm'."""
    if engine == "clef":
        return clef_engine.clef_verdict(claim, evidence)
    if not evidence:
        return {"verdict": "unverifiable", "confidence": 0.3,
                "explanation": "Nessuna evidenza trovata.", "source_urls": []}
    src = "\n".join(
        f"[{i+1}] {e['title']} — {e['snippet']} ({e['url']})"
        for i, e in enumerate(evidence))
    prompt = (
        "Sei un fact-checker rigoroso. Verifica l'affermazione usando SOLO le "
        "fonti numerate. Non usare conoscenza esterna. Se le fonti non sono "
        "sufficienti o contraddittorie, usa 'unverifiable'.\n"
        'Rispondi SOLO con JSON: {"verdict": "true|false|misleading|unverifiable",'
        ' "confidence": 0.0-1.0, "explanation": "max 2 frasi in italiano",'
        ' "source_urls": ["url che supportano il verdetto"]}\n\n'
        f"Affermazione: {claim}\n\nFonti:\n{src}"
    )
    try:
        out = _cf_chat(VERDICT_MODEL, [{"role": "user", "content": prompt}], 600)
        v = _parse_json(out)
    except Exception as exc:
        return {"verdict": "unverifiable", "confidence": 0.0,
                "explanation": f"errore modello: {exc}", "source_urls": []}
    if v.get("verdict") not in VERDICTS:
        v["verdict"] = "unverifiable"
    urls = [u for u in v.get("source_urls", []) if isinstance(u, str)]
    known = {e["url"] for e in evidence}
    v["source_urls"] = [u for u in urls if u in known] or [e["url"] for e in evidence[:2]]
    return v


EMOJI = {"true": "✅", "false": "❌", "misleading": "⚠️", "unverifiable": "❓"}


def check_text(text, max_claims=3, log=print):
    """Pipeline completa: claims -> evidenze -> verdetti."""
    t0 = time.time()
    claims = extract_claims(text, max_claims)
    log(f"🔍 {len(claims)} claim trovati in {time.time()-t0:.1f}s")
    results = []
    for c in claims:
        t1 = time.time()
        ev = search_evidence(c)
        v = verdict(c, ev)
        v.update({"claim": c, "evidence": ev, "elapsed": round(time.time()-t1, 1)})
        results.append(v)
        log(f"{EMOJI[v['verdict']]} ({v['confidence']:.2f}, {v['elapsed']}s) {c[:80]}")
    return {"input": text, "results": results,
            "total_elapsed": round(time.time()-t0, 1)}


def format_report(res):
    lines = [f"🧾 FactCheck ({res['total_elapsed']}s)"]
    for v in res["results"]:
        lines.append(f"\n{EMOJI[v['verdict']]} \"{v['claim']}\"")
        lines.append(f"   Verdetto: {v['verdict']} ({v['confidence']*100:.0f}%) — {v['explanation']}")
        for u in v["source_urls"][:2]:
            lines.append(f"   🔗 {u}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--demo":
        texts = [
            "L'Italia ha una popolazione di circa 59 milioni di abitanti.",
            "Silvio Berlusconi è stato Presidente della Repubblica Italiana.",
            "Nel 2026 il PIB italiano crescerà esattamente del 3,7%.",
        ]
        for t in texts:
            print("=" * 70)
            print(format_report(check_text(t)))
    else:
        print(format_report(check_text(" ".join(sys.argv[1:]))))
