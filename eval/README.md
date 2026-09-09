# Eval packs

Drop a YAML pack. Bigas runs the models. A pack may fetch a public URL and optionally web-search on a step. That is Bigas-side research, not an API in your product.

`vfa-living-analysis` is self-contained (prompts in the YAML). It evaluates that prompt suite plus Bigas web research. It does not replay VFA's Tavily citation chain or write to any customer workspace.

`okr-goal-loop` runs the production OKR tool loop against a frozen Green Promo Wear snapshot (no live GA4 or board). Baseline is `gemini:gemini-3.8-flash`.

```bash
python scripts/run_eval.py --pack vfa-living-analysis \
  --company "VC Field Assistant" --url https://vcfieldassistant.com --dry-run

python scripts/run_eval.py --pack okr-goal-loop --dry-run
```

Minimal third-party pack: `id`, `fixture.input` or `fixture.url`, and `steps[].prompt`. `research` and `prompt_from` are optional.
