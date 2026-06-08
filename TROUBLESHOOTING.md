# Troubleshooting — when output diverges

If your Claude session's output looks nothing like the canonical published version at https://intellidesk-nu.vercel.app/research, check this list before re-running.

## Symptom: PDF or Excel layout looks completely different from the published versions

Almost always caused by Claude using `anthropic-skills:pdf` or `anthropic-skills:xlsx` to produce freeform output instead of running `_schema/render_excel.py`.

**Fix:** Re-prompt Claude with: *"per CLAUDE.md, the Excel deliverable must come from `python _schema/render_excel.py --ticker <T> --category <AI or IDX>`. Do not use `anthropic-skills:xlsx`. Produce the three files in `output/<T>/`."*

## Symptom: Claude pasted the entire research in chat instead of producing files

The contract requires the deliverable to be the three files, not a chat dump.

**Fix:** *"per the output contract in CLAUDE.md, do not paste the report inline. Write the three files to `output/<T>/`, then return only the file paths + a one-paragraph headline."*

## Symptom: My target differs from the published version by a lot on the same ticker

This is expected if your assumptions differ. The math is deterministic given inputs; different inputs → different outputs.

**Fix:** Diff `<T>_inputs.json` between your output and the published version. The Vercel `/valuation` page shows every assumption with its reasoning. Open both Excel files and compare the Assumptions tab cell-by-cell. Whichever WACC, terminal g, or FCF projection differs is the source of the divergence.

If the assumptions match but the target differs, that's a bug. Tell the repo owner.

## Symptom: Excel has different number of tabs than 8

This means `render_excel.py` didn't run, or ran on an incomplete valuation JSON.

**Fix:** Check that `<T>_valuation.json` has the full structure (primary_method with calculation_trace, sensitivity, scenarios array with probabilities, weighted_contributions). If those fields are missing, re-run the compute script:

```bash
python _schema/dcf_compute.py --inputs output/<T>/<T>_inputs.json --output output/<T>/<T>_valuation.json
```

Then re-render:

```bash
python _schema/render_excel.py --ticker <T> --category AI
```

## Symptom: Excel "Scenarios" tab shows "WARNING: weights do not sum to 1.0"

The `<T>_inputs.json` has scenarios where the `probability` fields plus `blending_weights.cross_check` don't add to 1.0.

**Fix:** Open `<T>_inputs.json` and check each scenario's `probability`. They should sum with `blending_weights.cross_check` to exactly 1.0. Example:

```jsonc
{
  "scenarios": [
    {"label": "Bear", "probability": 0.10, ...},
    {"label": "Base", "probability": 0.55, ...},
    {"label": "Bull", "probability": 0.15, ...}
  ],
  "blending_weights": {"cross_check": 0.20}
}
// 0.10 + 0.55 + 0.15 + 0.20 = 1.00 ✓
```

## Symptom: One of the inputs has no reasoning shown

The compute script auto-attaches default reasoning when the LLM doesn't supply one, so this should only happen if you're using an old version of the compute script.

**Fix:** Make sure `_schema/dcf_compute.py` and `_schema/ddm_compute.py` are the latest version (pull from the ERFS repo). If still missing, the LLM probably supplied an `inputs.json` without per-component reasoning AND the script's default-reasoning fallback failed — open an issue.

## Symptom: Bear case obviously read the bull case (mirrors the bull's framing)

The `equity-bear-case` skill is instructed not to read the bull file. If Claude broke that, the cross-check is fake.

**Fix:** Re-invoke `equity-bear-case` skill in a fresh context. The skill has a hard rule that overrides chat history pressure. If the bear keeps mirroring the bull, the skill body needs to be tightened — file an issue.

## Symptom: My session can't find `_schema/dcf_compute.py`

The SessionStart hook only clones `himself65/finance-skills` into `.claude/skills/finance-skills/`. Your custom workspace files (including `_schema/`) live in the ERFS repo and should be present when you clone or fork.

**Fix:** If `_schema/` is missing, your fork is behind. Sync your fork on github.com (the "Sync fork" button on the fork page). Then re-open the Claude Code session.

## Symptom: yfinance returns "no data" for a ticker

Common for:
- New listings within the last few days
- Suspended / delisted tickers
- Mistyped symbols (use the correct exchange suffix: `.JK` for IDX, `.SI` for Singapore, `.NS` for India, `.HK` for Hong Kong, `.T` for Tokyo)

**Fix:** Verify the symbol on Yahoo Finance directly. If it's correct and yfinance still returns empty, the ticker may be in a delisting halt — that's a data gap, log it and continue without the price-anchored sections.

## How to validate that your session is following the contract

Sanity check after a research run completes:

```bash
# All three files should exist
ls output/<T>/
# Should show: <T>.json  <T>_inputs.json  <T>_valuation.json  <T>.xlsx

# Valuation JSON should have probability-weighted scenarios + trace
python -c "import json; d=json.load(open('output/<T>/<T>_valuation.json',encoding='utf-8')); print('blended:', d['blended_target']); print('weight total:', d.get('weight_total_check')); print('has trace:', bool(d['primary_method'].get('outputs',{}).get('calculation_trace') or d['primary_method'].get('calculation_trace')))"
```

If `blended` matches the headline Claude reported, if `weight_total` is 1.0, and if `has trace` is True, your session followed the contract.
