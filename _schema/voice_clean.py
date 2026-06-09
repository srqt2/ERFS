"""Voice-clean all 9 research JSONs in GER/output/. Removes em-dashes and common AI tells.

After this runs, build_and_deploy.py can safely sync GER -> IntelliDesk without
re-introducing AI tells.

This is the MECHANICAL pass (fast, deterministic, regex-based). A deeper
agent-driven prose rewrite can run separately for final polish.
"""

import json
import re
from pathlib import Path

GER_OUTPUT = Path(r"C:\Users\janua\Downloads\Equity Projects\Global Equity Research\output")
TICKERS = ["CLS", "AVGO", "NVDA", "COHR", "MU", "GEV", "NOW", "TSM", "VST"]


def smart_em_dash(text: str) -> str:
    """Replace em/en dashes with context-aware punctuation.

    Patterns and choice:
      ' — '  -> '. '  if next word starts uppercase; else '; '
      '— '   -> '; '
      ' —'   -> ';'
      Numeric ranges 'X — Y' stay readable: '$117 — $474' -> '$117 to $474'
    """
    # Numeric range: $123 — $456 (and 117.28 - 474.03 etc.) -> $123 to $456
    text = re.sub(r"(\$?\d[\d,\.]*)\s*[—–]\s*(\$?\d)", r"\1 to \2", text)

    # Standalone " — " between clauses
    def repl(m):
        after = m.group(1)
        # If next char is uppercase letter, use period + space + that letter
        if after and after[0].isupper():
            return ". " + after
        return "; "

    text = re.sub(r"\s*[—–]\s*([A-Za-z])", repl, text)

    # Any leftover em/en dashes get turned into commas
    text = text.replace("—", ",").replace("–", ",")
    return text


# Word-level AI tell substitutions. Case-preserving where possible.
AI_TELLS = [
    # (pattern, replacement, flags)
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
    # Hedge stacks
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
    # Cleanup double spaces and stranded commas
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r" {2,}", " ", out)
    # If we removed an opener (Moreover,) and left a sentence starting lowercase, capitalize
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


total_before, total_after = 0, 0
for t in TICKERS:
    src = GER_OUTPUT / t / f"{t}.json"
    if not src.exists():
        print(f"!! missing {src}")
        continue
    with open(src, encoding="utf-8") as f:
        raw = f.read()
    em_before = raw.count("—") + raw.count("–")
    data = json.loads(raw)

    cleaned = walk(data)
    out_text = json.dumps(cleaned, indent=2, ensure_ascii=False)
    em_after = out_text.count("—") + out_text.count("–")

    with open(src, "w", encoding="utf-8") as f:
        f.write(out_text)

    total_before += em_before
    total_after += em_after
    print(f"  {t:5s}  em/en-dashes: {em_before:>3} -> {em_after}")

print(f"\nTotal em/en-dashes: {total_before} -> {total_after}")
