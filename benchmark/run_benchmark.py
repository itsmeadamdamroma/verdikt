#!/usr/bin/env python3
"""Verdikt benchmark runner — 50 claim via API locale -> results.json.
Uso: python3 run_benchmark.py pre|post [--ids EN01,IT01]
Resume: i claim gia' presenti nel file di output vengono saltati.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = os.environ.get("VERDIKT_BASE", "http://127.0.0.1:8400")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = {"pre": "results.json", "post": "results_post.json"}


def post(path, payload, key=None, timeout=180):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode(),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run(phase, ids=None):
    claims = json.load(open(os.path.join(HERE, "claims.json")))
    if ids:
        claims = [c for c in claims if c["id"] in ids]
    out_path = os.path.join(HERE, OUT[phase])
    results = {}
    if os.path.exists(out_path):
        for r in json.load(open(out_path)):
            results[r["id"]] = r
    key = post("/v1/signup", {"email": f"bench-{phase}@local"})["api_key"]
    todo = [c for c in claims if c["id"] not in results]
    print(f"[{phase}] {len(todo)} claims da fare ({len(results)} gia' fatti)", flush=True)
    for i, c in enumerate(todo, 1):
        t0 = time.time()
        entry = {"id": c["id"], "lang": c["lang"], "cat": c["cat"],
                 "expected": c["expected"], "sent": c["claim"]}
        try:
            resp = post("/v1/check", {"text": c["claim"], "max_claims": 1}, key)
            r0 = (resp.get("results") or [{}])[0]
            entry.update({
                "api_claim": r0.get("claim", ""),
                "verdict": r0.get("verdict", "error"),
                "confidence": r0.get("confidence"),
                "probabilities": r0.get("probabilities", {}),
                "evidence_strength": r0.get("evidence_strength"),
                "n_sources": len(r0.get("sources") or []),
                "elapsed_s": round(time.time() - t0, 1),
            })
            ok = "HIT " if entry["verdict"] == c["expected"] else "MISS"
            print(f"[{i}/{len(todo)}] {ok} {c['id']} {c['expected']:<11} -> "
                  f"{entry['verdict']:<12} conf={entry['confidence']} "
                  f"src={entry['n_sources']} {entry['elapsed_s']}s", flush=True)
        except urllib.error.HTTPError as e:
            entry.update({"verdict": "error", "error": f"HTTP {e.code}"})
            print(f"[{i}/{len(todo)}] ERR {c['id']} HTTP {e.code}", flush=True)
        except Exception as e:
            entry.update({"verdict": "error", "error": str(e)[:200]})
            print(f"[{i}/{len(todo)}] ERR {c['id']} {str(e)[:80]}", flush=True)
        results[c["id"]] = entry
        json.dump(list(results.values()), open(out_path, "w"),
                  ensure_ascii=False, indent=1)
        time.sleep(1.5)  # pacing DDG
    print(f"\n[{phase}] totali: {len(results)} claim, file: {out_path}")
    score(results)


def score(results):
    vals = list(results.values())
    tot = len(vals)
    hit = sum(1 for r in vals if r["verdict"] == r["expected"])
    print(f"ACCURACY OVERALL: {hit}/{tot} = {hit/tot*100:.0f}%")
    for dim in ("lang", "cat"):
        print(f"  per {dim}:")
        for k in sorted({r[dim] for r in vals}):
            sub = [r for r in vals if r[dim] == k]
            h = sum(1 for r in sub if r["verdict"] == r["expected"])
            print(f"    {k:<7} {h}/{len(sub)} = {h/len(sub)*100:.0f}%")
    for k in ("true", "false", "misleading", "unverifiable", "error"):
        sub = [r for r in vals if r["expected"] == k]
        if not sub:
            continue
        h = sum(1 for r in sub if r["verdict"] == r["expected"])
        print(f"  expected={k:<12} {h}/{len(sub)} = {h/len(sub)*100:.0f}%")
    unv = [r for r in vals if r["verdict"] == "unverifiable"]
    print(f"  unverifiable: {len(unv)}/{tot} -> {[r['id'] for r in unv]}")
    err = [r for r in vals if r["verdict"] == "error"]
    if err:
        print(f"  ERRORI: {[(r['id'], r.get('error','')[:60]) for r in err]}")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "pre"
    ids = None
    if "--ids" in sys.argv:
        ids = set(sys.argv[sys.argv.index("--ids") + 1].split(","))
    run(phase, ids)
