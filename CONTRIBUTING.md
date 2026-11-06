# Contributing

Small project, small rules.

## What is welcome

- **Bug reports with a reproduction.** Claim text, expected verdict, actual verdict.
- **New benchmark claims.** `benchmark/claims.json` — add entries with a verified ground
  truth and a source that settles it. This is the most valuable contribution: the suite is
  what keeps the engine honest.
- **Fixes to the evidence pipeline** (search fallbacks, caching, source filtering).
- **Translations of the prompts** for languages other than English/Italian.

## What is not

- New dependencies for something the standard library does.
- A model swap without a benchmark run showing it is not worse.
- Features that make a verdict without evidence.

## Before you open a PR

```bash
python benchmark/run_benchmark.py     # must not increase directional errors
```

Directional errors (true↔false) must stay at **0**. A PR that improves the exact score but
introduces one directional error is rejected: for a fact-checker, confidently wrong is
worse than unsure.

## License

Contributions are accepted under AGPL-3.0.
