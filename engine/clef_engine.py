#!/usr/bin/env python3
"""Clef engine — verdicti calibrati con Cloudflare Clef (decision model).
Sostituisce/augmenta i verdicti LLM: probabilità reali per ogni opzione.
"""
import json
import os
import re
import urllib.request
import urllib.error

MODELS_JSON = os.environ.get("CF_MODELS_JSON", "/root/.openclaw/agents/main/agent/models.json")

VERDICT_OPTIONS = {
    "true": "The evidence clearly supports the claim",
    "false": "The evidence contradicts the claim",
    "misleading": "The claim is partially supported, distorted or exaggerated",
    "unverifiable": "The evidence is insufficient, irrelevant or contradictory",
}

CONFIDENCE_LEVELS = ["No basis", "Weak", "Moderate", "Strong"]


def _creds():
    """Account + token: env prima, poi models.json di OpenClaw."""
    token = os.environ.get("CF_API_TOKEN")
    acct = os.environ.get("CF_ACCOUNT_ID")
    if token and acct:
        return acct, token
    d = json.load(open(MODELS_JSON))
    cf = d["providers"]["cloudflare"]
    acc = re.search(r"/accounts/([a-f0-9]+)/", cf["baseUrl"]).group(1)
    return acc, cf["apiKey"]


def clef_verdict(claim, evidence, timeout=40):
    """Verdetto calibrato via Clef. Ritorna dict standard del progetto."""
    if not evidence:
        return {"verdict": "unverifiable", "confidence": 0.3,
                "explanation": "Nessuna evidenza trovata.",
                "probabilities": {"unverifiable": 1.0},
                "source_urls": []}

    src = "\n".join(
        f"[{i+1}] {e['title']} — {e['snippet']} (source: {e['url']})"
        for i, e in enumerate(evidence))

    state = (
        "You are fact-checking a claim against retrieved evidence ONLY. "
        "Do not use outside knowledge.\n\n"
        f"CLAIM: {claim}\n\nEVIDENCE:\n{src}"
    )
    questions = {
        "verdict": {
            "type": "choice",
            "instructions": ("Is the claim supported by the evidence? If the "
                             "claim combines multiple separate assertions and "
                             "the evidence confirms only some of them, the "
                             "verdict is misleading (or unverifiable if no "
                             "part is addressed) — never true."),
            "criteria": VERDICT_OPTIONS,
        },
        "strength": {
            "type": "score",
            "instructions": "How strong is the evidence for this verdict?",
            "criteria": CONFIDENCE_LEVELS,
        },
    }

    acc, key = _creds()
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/cloudflare/clef"
    body = json.dumps({"model": "clef", "state": state,
                       "questions": questions}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except Exception as exc:
        return {"verdict": "unverifiable", "confidence": 0.0,
                "explanation": f"clef error: {exc}", "probabilities": {},
                "source_urls": [e["url"] for e in evidence[:2]]}

    if not resp.get("success"):
        return {"verdict": "unverifiable", "confidence": 0.0,
                "explanation": f"clef api error: {resp.get('errors')}",
                "probabilities": {}, "source_urls": []}

    ans = resp["result"]["answers"]
    v = ans["verdict"]
    s = ans["strength"]

    verdict_choice = v.get("choice", "unverifiable")
    if verdict_choice not in VERDICT_OPTIONS:
        verdict_choice = "unverifiable"
    probs = v.get("probabilities", {})
    # confidence: probabilità della scelta fatta (calibrata) oppure score della strength
    conf = float(probs.get(verdict_choice, 0.0))
    strength_score = float(s.get("score", 0.0))

    # Spiegazione: generata dal claim+evidenza top (senza LLM: template)
    top_ev = evidence[0] if evidence else None
    expl = {
        "true": f"Evidenze ({len(evidence)} fonti) supportano l'affermazione. Prima fonte: {top_ev['title']}.",
        "false": f"Evidenze contraddicono l'affermazione. Prima fonte: {top_ev['title']}.",
        "misleading": f"Affermazione parzialmente supportata o distorta. Prima fonte: {top_ev['title']}.",
        "unverifiable": "Evidenze insufficienti o contraddittorie per decidere.",
    }[verdict_choice]

    return {
        "verdict": verdict_choice,
        "confidence": round(conf, 3),
        "evidence_strength": round(strength_score, 2),
        "explanation": expl,
        "probabilities": {k: round(float(x), 4) for k, x in probs.items()},
        "source_urls": [e["url"] for e in evidence if e["url"]][:3],
        "model": "clef",
    }


if __name__ == "__main__":
    import sys
    from engine import search_evidence
    claim = " ".join(sys.argv[1:]) or "L'Italia ha una popolazione di circa 59 milioni di abitanti."
    ev = search_evidence(claim)
    print("Claim:", claim)
    print("Evidence:", len(ev), "fonti")
    print(json.dumps(clef_verdict(claim, ev), indent=1, ensure_ascii=False))
