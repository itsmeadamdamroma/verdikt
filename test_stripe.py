#!/usr/bin/env python3
"""Test standalone logica Stripe: firma, upgrade_by_hash, checkout session.
Non tocca il servizio in esecuzione."""
import sys, time, hmac, hashlib, json, sqlite3, urllib.request
sys.path.insert(0, "/root/verdikt")
sys.path.insert(0, "/root/factchecker")

import api  # noqa: E402  (carica stripe.env)
from api import _stripe_verify
import db  # noqa: E402

secret = api.STRIPE["STRIPE_WEBHOOK_SECRET"]
payload = json.dumps({"type": "checkout.session.completed",
                      "data": {"object": {"client_reference_id": "ab" * 32}}}).encode()


def sign(pl, ts=None, secret_=None):
    ts = str(int(ts or time.time()))
    v1 = hmac.new((secret_ or secret).encode(),
                  ts.encode() + b"." + pl, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


ok_valid = _stripe_verify(sign(payload), payload, secret)
ok_tamper = _stripe_verify(sign(payload), payload + b"x", secret)
ok_wrongsec = _stripe_verify(sign(payload, secret_="whsec_wrong"), payload, secret)
ok_stale = _stripe_verify(sign(payload, ts=time.time() - 4000), payload, secret)
print(f"firma valida: {ok_valid}  manomessa: {ok_tamper}  "
      f"chiave sbagliata: {ok_wrongsec}  stale: {ok_stale}")
assert ok_valid and not ok_tamper and not ok_wrongsec and not ok_stale

req = urllib.request.Request(
    "http://localhost:8400/v1/signup",
    data=json.dumps({"email": "stripe-test@local"}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=15) as r:
    raw = json.loads(r.read())["api_key"]
h = hashlib.sha256(raw.encode()).hexdigest()
plan0 = db.verify(raw)
db.upgrade_by_hash(h)
plan1 = db.verify(raw)
print(f"piano prima: {plan0}  dopo: {plan1}")
assert plan0 == "free" and plan1 == "pro"
c = sqlite3.connect("/root/verdikt/verdikt.db")
c.execute("UPDATE keys SET active=0 WHERE key=?", (h,))
c.commit()
c.close()
print("chiave test disattivata")

sess = api._stripe_api("POST", "/v1/checkout/sessions", {
    "mode": "subscription",
    "client_reference_id": h,
    "line_items[0][price]": api.STRIPE["VERDIKT_STRIPE_PRICE_ID"],
    "line_items[0][quantity]": "1",
    "success_url": "https://verdikt.mazzagrp.com/?upgraded=1",
    "cancel_url": "https://verdikt.mazzagrp.com/#pricing",
})
print("checkout url:", sess["url"][:70], "...")
assert sess.get("client_reference_id") == h
assert sess["url"].startswith("https://checkout.stripe.com")
print("OK: firma verificata + upgrade ok + sessione checkout reale creata")
