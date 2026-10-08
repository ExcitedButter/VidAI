# vidai

- `agent/` — the agentic product-video generator (Python package `vidai`, tests, config).
  Run from inside it: `cd agent && python -m vidai.cli generate --image ... --url ... "..."`.
  Secrets live in `agent/.env` (never commit / upload).
- `web/` — product-page extraction: `web/extract_product.py <url>` pulls title, price, SKUs,
  seller, rating, description and images from a TikTok Shop PDP or a brand site into
  `web/results/<slug>/product.json` + `images/`. `web/results/tt_bissell/` is the BISSELL
  Little Green example.
