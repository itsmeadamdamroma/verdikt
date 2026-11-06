# Verdikt

**Fact-checking API. Text in, calibrated verdicts with real sources out.**

No API keys to buy from OpenAI. No subscription to start. Runs on Cloudflare Workers AI
(or any OpenAI-compatible endpoint), searches the open web for evidence, and returns a
verdict with the sources it used — plus a probability for each possible verdict, so you
can see how sure it is instead of trusting a single word.

```
true          0.965   "The Eiffel Tower is 330 metres tall."
                      → en.wikipedia.org/wiki/Eiffel_Tower

false         0.918   "The Great Wall of China is visible from space with the naked eye."
                      → en.wikipedia.org/wiki/Artificial_structures_visible_from_space

true          0.978   "Italy has a population of about 59 million."
                      → worldometers.info/world-population/italy-population
```

Every verdict carries `probabilities` for all four outcomes, so a caller can decide
"only act if confidence > 0.9" instead of getting a bare boolean.

---

## Quick start

```bash
git clone https://github.com/itsmeadamdamroma/verdikt && cd verdikt
cp .env.example .env      # put CF_ACCOUNT_ID and CF_API_TOKEN in it
docker compose up
```

Then:

```bash
curl -s localhost:8400/v1/signup -d '{"email":"you@example.com"}'
# → {"api_key":"vk_...","plan":"free","daily_limit":100}

curl -s localhost:8400/v1/check \
  -H "X-API-Key: vk_..." \
  -d '{"text":"The Eiffel Tower is 330 metres tall."}'
```

No Docker? `pip install -r requirements.txt && python api.py`.

**Credentials.** Verdikt uses Cloudflare Workers AI for both extraction and verdicts —
free tier is generous, so a self-hosted instance costs cents. Create a token with
*Workers AI → Read* at dash.cloudflare.com/profile/api-tokens. Any OpenAI-compatible
endpoint works too: set `CF_BASE_URL` to it.

---

## API

| Endpoint | Cost | What it does |
|---|---|---|
| `POST /v1/check` | 1 credit | `{text, max_claims}` → claims, verdicts, sources, probabilities |
| `POST /v1/check/url` | 2 credits | Same, from a public URL (articles, YouTube) |
| `POST /v1/check/audio` | 3 credits | Same, from an audio/video file (needs `faster-whisper`, optional) |
| `POST /v1/signup` | free | `{email}` → API key |
| `GET /v1/usage` | free | Quota for the calling key |
| `GET /health` | free | Liveness |

Auth: `X-API-Key: vk_...` or `Authorization: Bearer vk_...`.

Verdicts: `true`, `false`, `misleading`, `unverifiable`. Every response includes
`probabilities` over the four, `evidence_strength`, and the source URLs actually used.

---

## Benchmark

50 claims (25 EN / 25 IT), ground truth written by hand and versioned in
`benchmark/claims.json`. Categories: science, history, numbers, geography, sports.

| | Exact | Usable (± misleading/false) | **Directional errors** |
|---|---|---|---|
| Baseline | 50% | 60% | **0** |
| Current | **70%** | **88%** | **0** |

*Directional error* = saying `true` about something false, or the reverse. Verdikt makes
**none** on this suite, across two independent runs. The remaining misses sit on the
misleading/false boundary and two sports cases.

Reproduce it yourself:

```bash
python benchmark/run_benchmark.py    # writes results.json + a table
```

The suite is small and honest, not a leaderboard. It exists so changes can be measured
instead of argued about.

---

## Self-hosted vs hosted

| | Self-hosted (this repo) | Hosted |
|---|---|---|
| Price | free | free 100/day · Pro $19/mo · Team $49/mo |
| Your data | never leaves your machine | ours |
| Models | yours (CF, OpenAI-compatible) | included |
| Audio | install `faster-whisper` | included |
| Updates | `git pull` | automatic |
| Support | issues on GitHub | email |

Self-hosting is a few commands but it is *your* server to patch. That is the whole
trade: pay for someone else to run it, or run it yourself.

---

## How it works

```
text ──▶ extract claims (LLM, max N)
      ──▶ for each claim: search the web (DuckDuckGo, cached 24h)
      ──▶ verdict from evidence only (Cloudflare Clef, calibrated probabilities)
      ──▶ sources filtered to the ones actually retrieved
```

Two rules keep it honest:

1. **The verdict model never sees the open internet** — only the retrieved snippets,
   numbered. If the sources are insufficient it must answer `unverifiable`.
2. **Source URLs are filtered against what was actually fetched.** The model cannot
   invent a citation; if it returns a URL that was not in the evidence, it is dropped.

---

## License

AGPL-3.0. Use it, fork it, run it. If you offer it as a hosted service, publish your
changes — that is the only condition, and it is what keeps this project alive.

Commercial licensing for embedding Verdikt in a closed product: hello@mazzagrp.com.
