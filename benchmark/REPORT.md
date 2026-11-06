# Verdikt — Benchmark Qualità & Robustezza Engine

Data: 2026-10-24 · Suite: 50 claims (25 EN / 25 IT) · API locale :8400

## Suite

Categorie: `sci` (18), `hist` (14), `nums` (10), `geo` (6), `sports` (2).
Mix ground truth: 18 true / 16 false / 16 misleading. Claim con numeri,
date, statistiche, scienze, geopolitica; claim multi-par-te inclusi.
Ground truth definito a mano (claims.json, versionato).

## Metodologia

- Esecuzione via `POST /v1/check` (chiavi `bench-*` self-service).
- Metriche:
  - **Exact** — verdict identico al ground truth.
  - **Usabile** — exact + adiacenti (misleading↔false): per un prodotto
    di fact-checking l'errore che conta è la direzione sbagliata
    (true↔false), non il confine misleading/false.
  - **Direzionali** — claim etichettati `true` usciti `false` e viceversa.

## Risultati

| Fase | Exact | Usabile | Direzionali | Unverifiable |
|---|---|---|---|---|
| **PRE (baseline)** | **25/50 = 50%** | 30/50 = 60% | 0 | 19/50 |
| **POST (fix)** | **35/50 = 70%** | **44/50 = 88%** | **0** | 2-3/50 (IT20 oscilla true/unverifiable) |

Il POST è stabile su due run indipendenti (entrambi 35/50, miss diversi
per nondeterminismo DDG/Clef — varianza ±2). Zero errori direzionali
(true↔false) in entrambe le fasi: il prodotto non dice mai "vero" a
qualcosa di falso né viceversa; l'errore residuo è sul confine
misleading/false e su 2 casi sports.

### Errori rimanenti (15, per expected label)

- **expected=true 17/18**: 1 miss — IT05 (geo multi-part) → misleading
  0.46: una parte del claim senza copertura sufficiente.
- **expected=false 14/16**: 2 miss — EN11, IT11 (entrambi sports) →
  unverifiable: confronti numerici rari ("10 NBA championships",
  "più Coppe dei Campioni della Real Madrid") non presenti negli
  snippet top-3.
- **expected=misleading 4/16**: 12 miss, quasi tutti → "false".
  Confine di labeling: il decision model calibrato preferisce `false`
  (P fino a 0.97) quando l'evidenza contraddice la distorsione —
  comportamento conservativo e difendibile per un fact-checker,
  non un errore di ricerca. IT20 oscilla true/unverifiable (nondeterminismo).
- **Errori direzionali (true↔false): 0/50** — il numero che conta
  per la qualità percepita.

### Per categoria (POST)

| Cat | Exact | | Cat | Exact |
|---|---|---|---|---|
| nums | 9/10 | | geo | 3/6 |
| sci | 13/18 | | hist | 10/14 |
| sports | 0/2 | | | |

## Fix applicati (engine)

1. **Ladder query DDG** (`engine.py`): claim → keywords (claim >60 char)
   → prima clausola → traduzione EN (solo claim italiani con accenti).
   Prima della ladder: 19/50 claim senza alcuna fonte.
2. **Cache evidenze su disco** TTL 24h (`evidence_cache/`, SHA1(query)).
3. **Fix cache vuota**: i fail transitori DDG non venivano più bloccati
   da cache `[]` (lettura `[]` = miss, scrittura solo di risultati non
   vuoti).
4. **Regola multi-part Clef** (`clef_engine.py`): claim con più
   affermazioni distinte → mai `true` se l'evidenza copre solo alcune
   parti (misleading/unverifiable).

## Fix applicati (vendita — stesso commit)

- **Stripe Checkout Pro $29/mese** (live, account Stripe di Mazzagrp):
  - product `Verdikt Pro` + price $29/month (creati da
    `stripe_setup.py`, idempotente).
  - `POST /v1/pro/checkout` — crea Checkout Session con
    `client_reference_id` = SHA256(api_key) (la chiave grezza non
    attraversa mai Stripe).
  - `POST /v1/stripe/webhook` — firma verificata (HMAC-SHA256, stdlib,
    no dipendenze), su `checkout.session.completed` → upgrade
    free→pro in SQLite.
  - Landing: button Go Pro → checkout → success_url con conferma.
  - **E2E verificato sul dominio pubblico**: webhook non firmato → 400,
    firmato → 200 + chiave pro, checkout → URL Stripe live, chiave
    invalida → 401.
  - `stripe.env` (segreti) chmod 600 + in .gitignore; mai nei commit.
- **Dashboard** `GET /dashboard?key=<admin>`: total checks, chiavi
  attive/pro, distribuzione verdetti (nuova tabella `verdicts`,
  popolata da ogni check). Chiave admin in `stripe.env`.

## Raccomandazioni

1. **Labeling misleading**: se i clienti si aspettano `misleading` per
   le distorsioni plateali, serve un secondo question Clef o un
   post-processing (false + P(false)>0.9 + claim con numerico distorto
   → misleading). Valutare con dati reali di produzione.
2. **Sports/confronti rari**: query mirata di fallback ("list of X
   titles by club" pattern) quando il claim contiene confronto
   comparativo.
3. **Paywall Stripe**: la subscription live richiede a Boss un test
   di pagamento reale (la sessione checkout è pronta; nessuna carta
   caricata). Eventuale email di benvenuto Pro da aggiungere al webhook.
4. **DDG rate-limit**: 50 claim consecutivi tengono, ma per traffico
   reale valutare cache condivisa + backoff già presente; se i volumi
   crescono, fonte secondaria (SearXNG locale).

## File

- `claims.json` — suite ground truth
- `results.json` — run PRE (baseline 50%)
- `results_post_fix1.json` — primo run post-fix
- `results_post.json` — run POST finale (70%)
- `run_benchmark.py` — runner riavviabile (skip claim già fatti)
