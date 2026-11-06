#!/usr/bin/env python3
"""FactChecker LIVE — da file audio/video a report fact-check.
Uso: python3 live.py <file_audio> [--max-claims N]
Pipeline: faster-whisper (locale) -> estrazione claims -> evidenze DDG -> verdetto CF.
"""
import sys
import time

from engine import check_text, format_report


def transcribe(path, model_name="large-v3-turbo"):
    from faster_whisper import WhisperModel
    t0 = time.time()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(path, vad_filter=True, beam_size=5)
    text = " ".join(s.text.strip() for s in segments).strip()
    print(f"🎤 Trascrizione ({info.language}, {info.duration:.0f}s) in {time.time()-t0:.0f}s:")
    print(text[:400] + ("…" if len(text) > 400 else ""))
    return text


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    max_claims = 3
    for a in sys.argv[1:]:
        if a.startswith("--max-claims"):
            max_claims = int(a.split("=")[1]) if "=" in a else 3
    if not args:
        print(__doc__)
        sys.exit(1)
    text = transcribe(args[0])
    if not text:
        print("Nessun parlato rilevato.")
        sys.exit(0)
    res = check_text(text, max_claims=max_claims)
    print()
    print(format_report(res))
