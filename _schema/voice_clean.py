"""Voice-clean research narrative JSONs.

Removes em-dashes and common AI tells in place. Works against the friend's
local output/ directory — no hardcoded paths or ticker lists.

Usage:
    python _schema/voice_clean.py output/AVGO/AVGO.json
    python _schema/voice_clean.py output/CLS/CLS.json output/NVDA/NVDA.json

Or to clean every <TICKER>/<TICKER>.json under output/:
    python _schema/voice_clean.py
"""

import json
import re
import sys
from pathlib import Path


def smart_em_dash(text: str) -> str:
    """Replace em/en dashes with context-aware punctuation."""
    # Numeric range: $123 — $456 -> $123 to $456
    text = re.sub(r"(\$?\d[\d,\.]*)\s*[—–]\s*(\$?\d)", r"\1 to \2", text)

    # Standalone " — " between clauses
    def repl(m):
        after = m.group(1)
        if after and after[0].isupper():
            return ". " + after
        return "; "

    text = re.sub(r"\s*[—–]\s*([A-Za-z])", repl, text)
    text = text.replace("—", ",").replace("–", ",")
    return text


AI_TELLS = [
    (r"\bdelve into\b", "dig into", re.IGNORECASE),
    (r"\bdelving into\b", "digging into", re.IGNORECASE),
    (r"\btapestry\b", "mix", re.IGNORECASE),
    (r"\bintricate\b", "complex", re.IGNORECASE),
    (r"\bcomprehensive\b", "full", re.IGNORECASE),
    (r"\brobust\b", "solid", re.IGNORECASE),
    (r"\bnavigate\b", "work through", re.IGNORECASE),
    (r"\bnavigating\b", "working through", re.IGNORECASE),
    (r"\bunderpin(s|ned|ning)?\b", "supports", re.IGNORECASE),
    (r"\bbolster(s|ed|ing)?\b", "supports", re.IGNORECASE),
    (r"\bensure(s|d)?\b", "makes sure", re.IGNORECASE),
    (r"\bfacilitat(e|es|ed|ing)\b", "enables", re.IGNORECASE),
    (r"\ba testament to\b", "shows", re.IGNORECASE),
    (r"\bspeaks? to\b", "points to", re.IGNORECASE),
    (r"\bMoreover,?\s*", "", 0),
    (r"\bFurthermore,?\s*", "", 0),
    (r"\bAdditionally,?\s*", "", 0),
    (r"\bNotably,\s*", "", 0),
    (r"\bImportantly,\s*", "", 0),
    (r"\bSignificantly,\s*", "", 0),
    (r"It's worth noting\s+(that\s+)?", "", 0),
    (r"\bIn conclusion,?\s*", "", 0),
    (r"\bAt the end of the day,?\s*", "", 0),
    (r"\bgame[- ]changer\b", "step change", re.IGNORECASE),
    (r"\bparadigm shift\b", "regime change", re.IGNORECASE),
    (r"\bsea change\b", "regime change", re.IGNORECASE),
    (r"\bcould potentially\b", "could", re.IGNORECASE),
    (r"\blikely could\b", "could", re.IGNORECASE),
    (r"\bmay potentially\b", "may", re.IGNORECASE),
]


def fix_text(text):
    if not isinstance(text, str):
        return text
    out = smart_em_dash(text)
    for pat, repl, flags in AI_TELLS:
        out = re.sub(pat, repl, out, flags=flags)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r" {2,}", " ", out)
    out = re.sub(r"(\. )([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out.strip()


def walk(o):
    if isinstance(o, str):
        return fix_text(o)
    if isinstance(o, dict):
        return {k: walk(v) for k, v in o.items()}
    if isinstance(o, list):
        return [walk(x) for x in o]
    return o


def clean_file(path: Path) -> tuple:
    """Voice-clean a single JSON file in place. Returns (em_before, em_after)."""
    raw = path.read_text(encoding="utf-8")
    em_before = raw.count("—") + raw.count("–")
    data = json.loads(raw)
    cleaned = walk(data)
    out_text = json.dumps(cleaned, indent=2, ensure_ascii=False)
    em_after = out_text.count("—") + out_text.count("–")
    path.write_text(out_text, encoding="utf-8")
    return em_before, em_after


def main():
    args = sys.argv[1:]
    if args:
        paths = [Path(a) for a in args]
    else:
        # Default: scan workspace's output/ directory for all <TICKER>/<TICKER>.json
        workspace_root = Path(__file__).resolve().parent.parent
        output_dir = workspace_root / "output"
        if not output_dir.exists():
            print(f"No output/ directory found at {output_dir} and no explicit paths given. Nothing to do.")
            return
        paths = []
        for ticker_dir in sorted(output_dir.iterdir()):
            if ticker_dir.is_dir():
                p = ticker_dir / f"{ticker_dir.name}.json"
                if p.exists():
                    paths.append(p)

    if not paths:
        print("No JSON files found to clean.")
        return

    total_before, total_after = 0, 0
    for p in paths:
        if not p.exists():
            print(f"  !! missing {p}")
            continue
        b, a = clean_file(p)
        total_before += b
        total_after += a
        # Print just the ticker if possible
        label = p.stem if p.parent.name == p.stem else str(p)
        print(f"  {label:8s}  em/en-dashes: {b:>3} -> {a}")

    print(f"\nTotal em/en-dashes: {total_before} -> {total_after}")


if __name__ == "__main__":
    main()
