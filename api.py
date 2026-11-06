#!/usr/bin/env python3
"""Verdikt API — sellable fact-checking API on Clef (calibrated decision model).
Endpoints:
  GET  /                  -> landing page
  POST /v1/check          -> {text} -> claims + verdicts (needs X-API-Key)
  POST /v1/check/audio    -> multipart audio/video -> transcription + verdicts
  GET  /v1/usage          -> quota stats
  GET  /health            -> ok
"""
import asyncio
import hashlib
import html as html_lib
import hmac
import io
import ipaddress
import json
import os
import socket
import threading
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from engine import check_text, format_report  # noqa: E402
import db  # noqa: E402

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse, HTMLResponse, PlainTextResponse, FileResponse, RedirectResponse  # noqa: E402
from starlette.routing import Route
from starlette.middleware import Middleware  # noqa: E402
from starlette.requests import Request  # noqa: E402

PORT = int(os.environ.get("VERDIKT_PORT", "8400"))
SELF_URL = os.environ.get("VERDIKT_URL", f"http://92.4.223.12:{PORT}")


STRIPE_ENV_PATH = os.path.join(os.path.dirname(__file__), "stripe.env")


def _load_env(path):
    d = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k] = v
    except FileNotFoundError:
        pass
    return d


STRIPE = _load_env(STRIPE_ENV_PATH)

# ── rate limiting + anti-abuso (in-memory) ───────────────────────────
# ponytail: per-processo, si azzera al restart. Con più istanze serve Redis.
_RL = {}
_RL_LOCK = threading.Lock()


def _rl_hit(bucket, who, limit, window_s):
    """True se consentito. Finestra scorrevole per (bucket, who)."""
    now = time.time()
    key = (bucket, who)
    with _RL_LOCK:
        arr = [x for x in _RL.get(key, []) if now - x < window_s]
        if len(arr) >= limit:
            _RL[key] = arr
            return False
        arr.append(now)
        _RL[key] = arr
        if len(_RL) > 5000:  # pota le chiavi vecchie
            for k in list(_RL)[:1000]:
                _RL.pop(k, None)
    return True


def _client_ip(req):
    h = req.headers.get("cf-connecting-ip") or req.headers.get("x-forwarded-for", "")
    return (h.split(",")[0].strip() if h else
            (req.client.host if req.client else "?"))


_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")


def _stripe_api(method, path, fields=None):
    """Chiamata API Stripe con encoding form/query (stdlib, zero dipendenze)."""
    url = "https://api.stripe.com" + path
    if method == "GET" and fields:
        url += "?" + urllib.parse.urlencode(fields)
    data = (urllib.parse.urlencode(fields).encode()
            if (method == "POST" and fields) else None)
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": "Bearer " + STRIPE.get("STRIPE_SECRET_KEY", "")})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _stripe_verify(sig_header, payload, secret, tol=300):
    """Verifica firma Stripe: v1 = HMAC-SHA256(secret, t + '.' + payload)."""
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
        t = parts.get("t", "").strip()
        if abs(time.time() - float(t)) > tol:
            return False
        expected = hmac.new(secret.encode(),
                            t.encode() + b"." + payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts.get("v1", ""))
    except Exception:
        return False


async def pro_checkout(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = str(body.get("api_key", "")).strip()
    if not db.verify(key):
        return JSONResponse({"error": "invalid API key"}, status_code=401)
    price_id = STRIPE.get("VERDIKT_STRIPE_PRICE_ID", "")
    if not price_id:
        return JSONResponse({"error": "stripe not configured"}, status_code=503)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    try:
        sess = _stripe_api("POST", "/v1/checkout/sessions", {
            "mode": "subscription",
            "client_reference_id": key_hash,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": "https://verdikt.mazzagrp.com/?upgraded=1",
            "cancel_url": "https://verdikt.mazzagrp.com/#pricing",
        })
    except Exception as e:
        return JSONResponse({"error": "checkout error: " + str(e)[:160]},
                            status_code=502)
    return JSONResponse({"url": sess.get("url")})


async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secret = STRIPE.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret or not _stripe_verify(sig, payload, secret):
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    try:
        event = json.loads(payload)
    except Exception:
        return JSONResponse({"error": "bad payload"}, status_code=400)
    obj = event.get("data", {}).get("object", {})
    if event.get("type") == "checkout.session.completed":
        h = str(obj.get("client_reference_id", ""))
        if re.fullmatch(r"[0-9a-f]{64}", h):
            db.upgrade_by_hash(h)
    return JSONResponse({"received": True})


async def dashboard(request: Request):
    admin = STRIPE.get("VERDIKT_ADMIN_KEY", "")
    k = request.query_params.get("key", "")
    if not admin or not secrets.compare_digest(k, admin):
        return PlainTextResponse("forbidden", status_code=403)
    s = db.dashboard_stats()
    rows = "".join(f"<tr><td>{v}</td><td>{n}</td></tr>"
                   for v, n in sorted(s["verdicts"].items(),
                                      key=lambda x: -x[1]))
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Verdikt Dashboard</title>
<style>body{{font:14px/1.5 system-ui;background:#0d0d0f;color:#e8e8e8;
padding:2rem}}h1{{font-size:1.2rem}}td,th{{padding:.25rem .8rem;text-align:left}}
table{{border-collapse:collapse}}tr{{border-bottom:1px solid #2a2a2e}}
.big{{font-size:2rem;color:#c9a227}}</style></head><body>
<h1>Verdikt — dashboard</h1>
<p class="big">{s['total_checks']}</p><p>total checks</p>
<table><tr><th>active keys</th><th>pro keys</th></tr>
<tr><td>{s['active_keys']}</td><td>{s['pro_keys']}</td></tr></table>
<h3>verdict distribution</h3>
<table><tr><th>verdict</th><th>n</th></tr>{rows}</table>
</body></html>"""
    return HTMLResponse(html)


def _key_from(req: Request):
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return req.headers.get("x-api-key", "").strip()


def _auth(req, cost=1):
    """Returns (raw_key, plan) or error JSONResponse."""
    raw = _key_from(req)
    if not raw:
        return None, JSONResponse({"error": "missing API key. Get one at "
                                   + SELF_URL}, status_code=401)
    plan = db.verify(raw)
    if not plan:
        return None, JSONResponse({"error": "invalid or inactive API key"},
                                  status_code=401)
    limit = db.PLANS.get(plan, 100)
    used, ok = db.check_quota(raw, limit)
    if not ok:
        return None, JSONResponse({
            "error": "daily quota exceeded", "plan": plan,
            "used": used, "limit": limit}, status_code=429)
    return raw, None


async def _lottie_js(req: Request):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "lottie.min.js"), media_type="application/javascript")


async def _static_file(req: Request):
    base = os.path.realpath(os.path.join(os.path.dirname(__file__), "static"))
    full = os.path.realpath(os.path.join(base, "lottie", req.path_params["path"]))
    if not (full.startswith(base + os.sep) and os.path.isfile(full)):
        return PlainTextResponse("not found", status_code=404)
    ctype = "application/javascript" if full.endswith(".js") else ("application/json" if full.endswith(".json") else "text/plain")
    return FileResponse(full, media_type=ctype)


async def landing(request):
    html = open(os.path.join(os.path.dirname(__file__), "static",
                             "index.html")).read()
    return HTMLResponse(html)


async def health(request):
    return PlainTextResponse("ok")


async def check(request: Request):
    if not _rl_hit("api_ip", _client_ip(request), 120, 60):
        return JSONResponse({"error": "rate limit: 120 requests/minute per IP"}, status_code=429)
    raw, err = _auth(request, cost=1)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    text = str(body.get("text", "")).strip()
    max_claims = min(int(body.get("max_claims", 3)), 10)
    if not text:
        return JSONResponse({"error": "empty 'text'"}, status_code=400)
    if len(text) > 10000:
        return JSONResponse({"error": "text too long (max 10000 chars)"},
                            status_code=400)
    t0 = time.time()
    res = check_text(text, max_claims=max_claims, log=lambda *_: None)
    for v in res["results"]:
        db.log_verdict(v["verdict"])
        db.log_check(raw, v["claim"], v["verdict"], v["confidence"],
                     int((time.time() - t0) * 1000), len(v["source_urls"]))
    stats = db.usage_stats(raw)
    return JSONResponse({
        "input": res["input"],
        "results": [{
            "claim": v["claim"],
            "verdict": v["verdict"],
            "confidence": v["confidence"],
            "evidence_strength": v.get("evidence_strength"),
            "explanation": v["explanation"],
            "probabilities": v.get("probabilities", {}),
            "sources": v["source_urls"],
            "elapsed_s": v["elapsed"],
        } for v in res["results"]],
        "total_elapsed_s": res["total_elapsed"],
        "quota": stats,
    })


async def check_audio(request: Request):
    if not _rl_hit("api_ip", _client_ip(request), 120, 60):
        return JSONResponse({"error": "rate limit: 120 requests/minute per IP"}, status_code=429)
    t0 = time.time()
    raw, err = _auth(request, cost=3)
    if err:
        return err
    form = await request.form()
    up = form.get("file")
    if up is None:
        return JSONResponse({"error": "multipart field 'file' required"},
                            status_code=400)
    data = await up.read()
    if len(data) > 25 * 1024 * 1024:
        return JSONResponse({"error": "file too large (max 25MB)"},
                            status_code=413)
    max_claims = min(int(form.get("max_claims", "3") or 3), 10)
    suffix = os.path.splitext(up.filename or "audio.ogg")[1] or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name
    try:
        try:
            from live import transcribe
        except Exception as exc:  # faster-whisper non installato
            return JSONResponse(
                {"error": "audio transcription not enabled on this instance",
                 "hint": "pip install faster-whisper  (and ffmpeg) — optional extra",
                 "detail": str(exc)[:120]},
                status_code=501)
        text = transcribe(path)
    finally:
        os.unlink(path)
    if not text:
        return JSONResponse({"error": "no speech detected"}, status_code=422)
    res = check_text(text, max_claims=max_claims, log=lambda *_: None)
    for v in res["results"]:
        db.log_verdict(v["verdict"])
        db.log_check(raw, v["claim"], v["verdict"], v["confidence"],
                     int((time.time() - t0) * 1000), len(v["source_urls"]))
    stats = db.usage_stats(raw)
    return JSONResponse({
        "transcript": text,
        "results": [{
            "claim": v["claim"],
            "verdict": v["verdict"],
            "confidence": v["confidence"],
            "evidence_strength": v.get("evidence_strength"),
            "explanation": v["explanation"],
            "probabilities": v.get("probabilities", {}),
            "sources": v["source_urls"],
            "elapsed_s": v["elapsed"],
        } for v in res["results"]],
        "total_elapsed_s": res["total_elapsed"],
        "quota": stats,
    })



CONSOLE_PASS = STRIPE.get("VERDIKT_CONSOLE_PASS", "")
CONSOLE_COOKIE = "vk_console"


def _console_token() -> str:
    return hmac.new(CONSOLE_PASS.encode(), b"verdikt-console-v1", hashlib.sha256).hexdigest()


def _console_ok(req: Request) -> bool:
    if not CONSOLE_PASS:
        return False
    got = req.cookies.get(CONSOLE_COOKIE, "")
    return bool(got) and secrets.compare_digest(got, _console_token())


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Verdikt · Sign in</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#05070d;color:#e8ecf4;
 font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 background-image:linear-gradient(rgba(52,211,153,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(52,211,153,.05) 1px,transparent 1px);
 background-size:64px 64px}
form{width:min(92vw,360px);border:1px solid rgba(52,211,153,.18);border-radius:12px;padding:26px;
 background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01))}
h1{font:700 13px/1 ui-monospace,Menlo,monospace;letter-spacing:.16em;text-transform:uppercase;margin:0 0 4px}
h1 span{color:#34d399}
p.s{font:11px ui-monospace,Menlo,monospace;color:#7c8798;margin:0 0 18px;letter-spacing:.06em}
input{width:100%;box-sizing:border-box;background:#070b10;border:1px solid rgba(255,255,255,.14);color:#e8ecf4;
 border-radius:8px;padding:11px;font:13px ui-monospace,Menlo,monospace;outline:none}
input:focus{border-color:#34d399;box-shadow:0 0 0 3px rgba(52,211,153,.12)}
button{margin-top:12px;width:100%;border:0;border-radius:8px;padding:11px;background:#34d399;color:#04120c;
 font:600 13px -apple-system,sans-serif;cursor:pointer}
.e{color:#f87171;font:11px ui-monospace,Menlo,monospace;margin:10px 0 0}
</style></head><body>
<form method="post" action="/app/login">
  <h1>Verdikt <span>//</span> Console</h1>
  <p class="s">Private access</p>
  <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password" required>
  <button type="submit">Unlock</button>
  <!--err-->
</form></body></html>"""


async def app_page(request: Request):
    if not _console_ok(request):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "app.html"),
                        media_type="text/html")


async def app_login(request: Request):
    form = await request.form()
    pw = str(form.get("password", ""))
    if not CONSOLE_PASS or not secrets.compare_digest(pw, CONSOLE_PASS):
        await asyncio.sleep(1.2)  # ponytail: fixed brake, add per-IP lockout if this ever gets scanned
        return HTMLResponse(LOGIN_HTML.replace("<!--err-->", '<p class="e">Wrong password.</p>'),
                            status_code=401)
    resp = RedirectResponse("/app", status_code=303)
    resp.set_cookie(CONSOLE_COOKIE, _console_token(), max_age=60 * 60 * 24 * 30,
                    httponly=True, secure=True, samesite="lax")
    return resp

async def owner_key(request: Request):
    """Returns the owner key to the password-gated console only."""
    if not _console_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    raw = STRIPE.get("VERDIKT_OWNER_KEY", "")
    if not raw:
        return JSONResponse({"error": "owner key not configured"}, status_code=404)
    return JSONResponse({"api_key": raw, "plan": db.verify(raw) or "unknown",
                         "daily_limit": db.PLANS.get("owner", 1000000)})



# ── URL extraction (articles + YouTube) ─────────────────────────────
_PRIVATE_HOST = re.compile(r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|169\.254\.|\[?::1\]?$)")


def _safe_url(url: str) -> bool:
    """Blocca SSRF: schema, hostname E indirizzi risolti (anti DNS-rebinding)."""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    if _PRIVATE_HOST.match(host):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except Exception:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _youtube_transcript(url: str) -> str:
    tmp = tempfile.mkdtemp(prefix="vk_yt_")
    try:
        subprocess.run(["yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
                        "--sub-langs", "en.*,it.*", "--sub-format", "vtt",
                        "--no-warnings", "--no-playlist", "-o", os.path.join(tmp, "%(id)s.%(ext)s"),
                        url], capture_output=True, timeout=120)
        files = [f for f in os.listdir(tmp) if f.endswith(".vtt")]
        if not files:
            return ""
        raw = open(os.path.join(tmp, files[0]), encoding="utf-8", errors="ignore").read()
        lines = []
        for ln in raw.splitlines():
            if "-->" in ln or ln.startswith(("WEBVTT", "Kind:", "Language:")) or not ln.strip():
                continue
            ln = re.sub(r"<[^>]+>", "", ln).strip()
            if ln and (not lines or lines[-1] != ln):
                lines.append(ln)
        return " ".join(lines)[:12000]
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


def _article_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; VerdiktBot/1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read(2_000_000).decode("utf-8", errors="ignore")
    html = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    paras = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.S | re.I)
    text = " ".join(re.sub(r"<[^>]+>", " ", p) for p in paras)
    text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) < 120:
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text).strip()
    return text[:12000]


def extract_url_text(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        t = _youtube_transcript(url)
        if t:
            return t
        return ""
    return _article_text(url)


async def check_url(request: Request):
    if not _rl_hit("api_ip", _client_ip(request), 120, 60):
        return JSONResponse({"error": "rate limit: 120 requests/minute per IP"}, status_code=429)
    t0 = time.time()
    raw, err = _auth(request, cost=2)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    url = str(body.get("url", "")).strip()
    if not _safe_url(url):
        return JSONResponse({"error": "invalid or private URL"}, status_code=400)
    max_claims = min(int(body.get("max_claims", 3)), 10)
    try:
        text = extract_url_text(url)
    except urllib.error.HTTPError as e:
        friendly = {401: "that site blocks automated readers (login or paywall)",
                    403: "that site blocks automated readers (paywall or bot protection)",
                    404: "that page does not exist",
                    429: "that site is rate-limiting us, try again in a minute"}.get(
                        e.code, "that site returned an error (%d)" % e.code)
        return JSONResponse({"error": friendly}, status_code=422)
    except Exception as e:
        return JSONResponse({"error": "could not read that link: " + str(e)[:120]}, status_code=422)
    if not text or len(text) < 40:
        return JSONResponse({"error": "no readable text found at that link (paywall, video without captions, or JS-only page)"},
                            status_code=422)
    res = check_text(text, max_claims=max_claims, log=lambda *_: None)
    for v in res["results"]:
        db.log_verdict(v["verdict"])
        db.log_check(raw, v["claim"], v["verdict"], v["confidence"],
                     int((time.time() - t0) * 1000), len(v["source_urls"]))
    return JSONResponse({
        "source_url": url,
        "extracted_chars": len(text),
        "results": [{
            "claim": v["claim"], "verdict": v["verdict"], "confidence": v["confidence"],
            "evidence_strength": v.get("evidence_strength"), "explanation": v["explanation"],
            "probabilities": v.get("probabilities", {}), "sources": v["source_urls"],
            "elapsed_s": v["elapsed"],
        } for v in res["results"]],
        "total_elapsed_s": res["total_elapsed"],
        "quota": db.usage_stats(raw),
    })


async def checks_history(request: Request):
    raw = _key_from(request)
    if not raw or not db.verify(raw):
        return JSONResponse({"error": "invalid API key"}, status_code=401)
    return JSONResponse({"checks": db.recent_checks(raw, 50),
                         "usage": db.usage_stats(raw)})


async def usage(request: Request):
    raw = _key_from(request)
    if not raw or not db.verify(raw):
        return JSONResponse({"error": "invalid API key"}, status_code=401)
    return JSONResponse(db.usage_stats(raw))


async def signup(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = str(body.get("email", ""))[:200].strip()
    ip = _client_ip(request)
    # 3 chiavi per IP al giorno, 200 al giorno in totale: senza questo, un loop
    # scriptato crea chiavi infinite e ognuna ha 100 check gratis.
    if not _rl_hit("signup_ip", ip, 3, 86400):
        return JSONResponse({"error": "too many keys from this address today"}, status_code=429)
    if not _rl_hit("signup_global", "all", 200, 86400):
        return JSONResponse({"error": "daily signup limit reached, try tomorrow"}, status_code=429)
    if email and not _EMAIL_RE.match(email):
        return JSONResponse({"error": "invalid email"}, status_code=400)
    raw = db.gen_key("free", email)
    return JSONResponse({
        "api_key": raw,
        "plan": "free",
        "daily_limit": db.PLANS["free"],
        "note": "Keep this key safe. Upgrade to pro for 5000 checks/day.",
    })


routes = [
    Route("/", landing),
    Route("/lottie.min.js", _lottie_js),
    Route("/lottie/{path:path}", _static_file),
    Route("/health", health),
    Route("/v1/check", check, methods=["POST"]),
    Route("/v1/check/audio", check_audio, methods=["POST"]),
    Route("/v1/usage", usage),
    Route("/v1/signup", signup, methods=["POST"]),
    Route("/v1/pro/checkout", pro_checkout, methods=["POST"]),
    Route("/v1/stripe/webhook", stripe_webhook, methods=["POST"]),
    Route("/dashboard", dashboard),
    Route("/app", app_page),
    Route("/app/login", app_login, methods=["POST"]),
    Route("/v1/checks", checks_history),
    Route("/v1/owner/key", owner_key),
    Route("/v1/check/url", check_url, methods=["POST"]),
]

class SecurityHeaders:
    """Header di sicurezza su ogni risposta (API + pagine)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers") or [])
                extra = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"no-referrer",
                    b"permissions-policy": b"geolocation=(), microphone=(), camera=()",
                    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
                }
                for k, v in extra.items():
                    headers.setdefault(k, v)
                message["headers"] = list(headers.items())
            await send(message)

        return await self.app(scope, receive, _send)


app = Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
