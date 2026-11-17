# ads-generator

One brief in, a full ad set out: angles and copy from a text model, clean art from an image
model (with real product references), deterministic Pillow overlays, platform exports, an A/B
plan and a cost manifest.

```
brief + brand kit
  -> text model: 3 concepts (problem / proof / offer), 3 headlines each, platform copy, A/B plan
  -> image model: one clean 2K render per concept x ratio, product + logo as references
  -> Pillow: logo TL, headline + subhead BL, CTA pill BR, safe margins, 3 copy variants
  -> out/<campaign>/: clean/, finals/, display/, copy.md, ab-plan.md, manifest.json
```

## Install

```bash
pip install -r requirements.txt
cp .env.example .env      # add OPENROUTER_API_KEY
python make_assets.py     # dummy logo + product photo for the example brand
```

## Use

```bash
# prompts and copy only, zero images
python ads.py --campaign campaigns/example.yaml --dry-run

# one concept, one ratio — cheapest real smoke test
python ads.py --campaign campaigns/example.yaml --only concept-1 --ratios 1:1 --budget 1

# full run
python ads.py --campaign campaigns/example.yaml
```

| flag | effect |
| --- | --- |
| `--campaign PATH` | campaign brief (required) |
| `--dry-run` | text model call + prompts + planned files, no images |
| `--only concept-1` | render a subset (repeatable) |
| `--ratios 1:1,4:5` | override the matrix |
| `--resolution 1K` | override `IMAGE_RESOLUTION` for this run |
| `--budget 1` | override `MAX_COST_USD` for this run |
| `--force` | overwrite existing clean/final files |
| `--brand` | brand folder under `brands/` (default: `campaign.brand`) |
| `--brand-path` | explicit brand.yaml instead of `brands/<campaign.brand>/brand.yaml` |

## Environment

| var | default | notes |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | required |
| `IMAGE_MODEL` | `google/gemini-nano-banana-2.1` | any model on `/api/v1/images/models` |
| `TEXT_MODEL` | `deepseek/deepseek-v4.1-flash` | copy + A/B plan |
| `IMAGE_RESOLUTION` | `2K` | `1K`, `2K`, `4K` |
| `OUTPUT_DIR` | `out` | |
| `MAX_COST_USD` | `5` | hard stop, checked before each render |
| `CONCURRENCY` | `3` | parallel image calls |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | escape hatch |

### Changing the image model

Set `IMAGE_MODEL` and nothing else. At startup the script reads
`/api/v1/images/models/<model>/endpoints` and validates the requested aspect ratios and
resolution against what that model actually declares. If the new model can't do `4:5`, you get

```
error: aspect ratio(s) 4:5 not supported by this image model. Supported: 1:1, 16:9, ...
```

instead of a silent bad crop. Parameters the model doesn't declare (e.g. `seed`, `quality`)
are never sent.

### Changing the text model

`TEXT_MODEL` is enough. The client prefers `response_format: json_schema` with
`provider.require_parameters`, falls back to `json_object`, and finally does one repair
turn. If a model is sloppy at Spanish, try `google/gemini-3.8-flash`.

## Briefs

`campaigns/<name>.yaml` — required: `product`, `description`, `benefits` (3+), `offer`,
`audience`, `objective` (`awareness|conversion`), `cta`, `url`. Optional: `brand`, `language`
(`es` default), `pain_points`, `differentiators`, `objections`, `tone`, `disclaimer`, `refs`
(product photos), `concepts` (default 3, max 6).

`brands/<name>/brand.yaml` — `name`, `logo`, `colors` (`primary`, `accent`, `on_accent`,
`text`), `fonts` (`headline`, `body`), `tone`, `disclaimer`, `default_cta`. Paths resolve from
the repo root. Fonts default to Roboto when omitted.

## Output

```
out/<campaign>/
  plan.json                     what the text model returned (concepts + A/B plan)
  clean/concept-1-4x5.png       2K art, full size, no copy
  finals/concept-1-4x5-A.png    1080x1350 overlay export (A/B/C per art)
  display/concept-1-1200x628-A.jpg
  copy.md                       headlines, primary text, short headline, CTA, prompt
  ab-plan.md                    waves, hypotheses, metrics, files to upload
  manifest.json                 prompt, model, refs, cost, size, status per asset
```

Ratios and export sizes: `1:1` 1080×1080, `4:5` 1080×1350, `9:16` 1080×1920, `16:9` 1920×1080,
plus a `1.91:1` 1200×628 Display crop made from the 16:9 art (the model has no 1.91:1 ratio, so
it is cropped). Display files are only produced when `16:9` is in the ratio set.

Every export is center-cropped to its target aspect before the overlay, so a swapped model that
ignores `aspect_ratio` cannot distort the creative — it gets cropped, not squashed.

Product photos listed in `refs` are sent as `input_references` (downscaled to 1600px, EXIF
corrected, max 14). The logo is composited by Pillow and never sent to the model, which avoids
the model reproducing it next to the overlay.

## Cost

The image model bills per output token (~$0.03–0.15 per 2K render), so a 3-concept × 4-ratio
run is roughly **$0.8–1.8**. Copy is ≈ $0.005. `manifest.json` records the exact `usage.cost`
per image; the budget guard uses a conservative prior before the first call. Totals cover the
current run only — cached art is not re-billed, so a re-run without `--force` costs just the
text call.

## Tests

```bash
pytest -q
```

Covers safe-area geometry, CTA/copy collision, Display crop size, and reference downscaling.
No API key needed.
