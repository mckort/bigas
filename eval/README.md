# Eval packs

Drop a YAML pack. Bigas runs the models. A pack may fetch a public URL and optionally web-search on a step. That is Bigas-side research, not an API in your product.

`vfa-living-analysis` evaluates the VC Field Assistant **prompt suite** plus Bigas web research. It does not replay VFA's Tavily citation chain or write to any customer workspace.

```bash
python scripts/run_eval.py --pack vfa-living-analysis \
  --company "VC Field Assistant" --url https://vcfieldassistant.com --dry-run
```

Minimal third-party pack: `id`, `fixture.input` or `fixture.url`, and `steps[].prompt`. `research` is optional.
