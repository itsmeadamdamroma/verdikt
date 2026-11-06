# Security

## Reporting

Email **hello@mazzagrp.com**. Do not open a public issue for a vulnerability.
We answer within 72 hours.

## Threat model of a self-hosted instance

Verdikt is a small service that talks to two external systems (a model endpoint and a
search engine) and stores API keys hashed in SQLite. Things to know:

- **API keys are stored as SHA-256 hashes**, never in plaintext. A leaked database does
  not leak usable keys.
- **`/v1/check/url` fetches user-supplied URLs.** Private hosts are blocked (SSRF guard),
  but if you expose this endpoint publicly, put it behind a proxy with egress rules.
- **The database is not encrypted at rest.** Encrypt the volume if you store anything
  sensitive alongside it.
- **No rate limiting beyond the daily quota.** Put Cloudflare or nginx in front if you
  expose it to the internet.
- **Stripe keys live in `stripe.env`, gitignored.** If you fork, never commit it.

## Supported versions

Only the current `main`. This is a small project, not an LTS product.
