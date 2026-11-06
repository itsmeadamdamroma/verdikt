#!/usr/bin/env python3
"""Setup Stripe one-shot per Verdikt Pro $29/mese.
Crea product+price+webhook endpoint (idempotente) e scrive /root/verdikt/stripe.env
chmod 600. NON tocca i product/price di Gino.
"""
import json
import os
import stat
import urllib.parse
import urllib.request

GINO_ENV = "/root/.openclaw/workspace/credentials/stripe_gino.env"
OUT_ENV = "/root/verdikt/stripe.env"
WEBHOOK_URL = "https://verdikt.mazzagrp.com/v1/stripe/webhook"


def kv(path):
    d = {}
    for line in open(path):
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k] = v
    return d


def api(method, path, fields=None, key=""):
    url = "https://api.stripe.com" + path
    if method == "GET" and fields:
        url += "?" + urllib.parse.urlencode(fields)
    data = urllib.parse.urlencode(fields).encode() if (method == "POST" and fields) else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    key = kv(GINO_ENV)["STRIPE_SECRET_KEY"]
    assert key.startswith("sk_"), "no STRIPE_SECRET_KEY in gino env"

    # product (idempotente: lista tutto e filtra client-side — l'API non filtra per name)
    prods = api("GET", "/v1/products", {"limit": "100"}, key)["data"]
    prods = [p for p in prods if p.get("name") == "Verdikt Pro"]
    if prods:
        prod_id = prods[0]["id"]
        print("product esistente:", prod_id)
    else:
        p = api("POST", "/v1/products", {
            "name": "Verdikt Pro",
            "description": "Verdikt Pro — 5000 fact-checks/day"}, key)
        prod_id = p["id"]
        print("product creato:", prod_id)

    # price $29/mese (idempotente sul product)
    prices = api("GET", "/v1/prices", {"limit": "100", "product": prod_id}, key)["data"]
    price = next((p for p in prices if p.get("recurring")
                  and p.get("unit_amount") == 2900
                  and p.get("currency") == "usd"), None)
    if price:
        price_id = price["id"]
        print("price esistente:", price_id)
    else:
        price = api("POST", "/v1/prices", {
            "product": prod_id,
            "currency": "usd",
            "unit_amount": "2900",
            "recurring[interval]": "month",
        }, key)
        price_id = price["id"]
        print("price creato:", price_id)

    # webhook endpoint (idempotente per URL)
    eps = api("GET", "/v1/webhook_endpoints", {"limit": "100"}, key)["data"]
    ep = next((e for e in eps if e["url"] == WEBHOOK_URL), None)
    if ep:
        whsec, ep_id = ep["secret"], ep["id"]
        print("webhook endpoint esistente:", ep_id)
    else:
        ep = api("POST", "/v1/webhook_endpoints", {
            "url": WEBHOOK_URL,
            "enabled_events[0]": "checkout.session.completed",
        }, key)
        whsec, ep_id = ep["secret"], ep["id"]
        print("webhook endpoint creato:", ep_id)

    with open(OUT_ENV, "w") as f:
        f.write(f"STRIPE_SECRET_KEY={key}\n"
                f"VERDIKT_STRIPE_PRICE_ID={price_id}\n"
                f"STRIPE_WEBHOOK_SECRET={whsec}\n")
    os.chmod(OUT_ENV, stat.S_IRUSR | stat.S_IWUSR)
    print(f"scritto {OUT_ENV} (chmod 600)")


if __name__ == "__main__":
    main()
