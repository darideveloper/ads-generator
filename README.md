# ads-generator

One brief in, a full ad set out: three layouts, copy from a text model, deterministic Pillow
composition, platform exports, an A/B plan and a cost manifest.

```
brief + brand kit
  -> text model: concepts (problem / proof / offer), 3 headlines + 3 on-image lines, A/B plan
  -> layout: type (brand canvas, 0 cost) | capture (real screenshot) | photo (AI art, opt-in)
  -> Pillow: grid, frame, logo, kicker, statement, CTA — measured before drawn
  -> out/<campaign>/: finals/, display/, contact-sheet.png, copy.md, ab-plan.md, manifest.json
```

The default layout draws the ad itself: **no image model, no cost**. Photography is opt-in
because in a feed a typographic card on carbon competes better than a stock-looking photo, and
because a cheap test should not depend on a paid render.

## Install

```bash
pip install -r requirements.txt
cp .env.example .env      # add OPENROUTER_API_KEY
python make_assets.py     # dummy logo + product photo for the example brand (photo layout)
```

## Use

```bash
# typographic set for the real campaign: 1 concept, 1:1 + 4:5, A/B, zero image cost
python ads.py --campaign campaigns/daridev-turismo.yaml

# copy and planned files only, nothing rendered
python ads.py --campaign campaigns/daridev-turismo.yaml --dry-run

# photo layout (costs money per render)
python ads.py --campaign campaigns/example.yaml --ratios 1:1 --budget 1
```

| flag | effect |
| --- | --- |
| `--campaign PATH` | campaign brief (required) |
| `--layout type\|capture\|photo` | override the campaign's layout |
| `--variants AB\|ABC` | which on-image variants to render (default `AB`) |
| `--ratios 1:1,4:5` | override the matrix (default `1:1,4:5`) |
| `--only concept-1` | render a subset (repeatable) |
| `--dry-run` | text model call + planned files, no renders |
| `--force` | overwrite existing files instead of reusing them |
| `--resolution 1K` | override `IMAGE_RESOLUTION` for this run (photo only) |
| `--budget 1` | override `MAX_COST_USD` for this run (photo only) |
| `--brand` / `--brand-path` | brand folder under `brands/`, or an explicit brand.yaml |

## Layouts

**`type` (default)** — paper, ice grid, registration frame, logo, kicker + rule, statement,
CTA block. The headline starts at 240 px and shrinks until the copy fits, so a three-word
statement fills the frame and an eight-word one still fits three lines. No images, no model
call, **0 USD**.

**`capture`** — same canvas, with a real screenshot in the slot above the statement. The
screenshot keeps its own proportion: it is scaled down to fit, never cropped (cutting a UI at a
different aspect removes the columns that carry the numbers) and never upscaled. One file per
concept, `capture:` for all of them or `captures:` in concept order.

**`photo`** — AI art with the copy on a flat carbon panel. The old gradient scrim is gone: the
system forbids gradients, so the wash is one hard-edged panel.

## Brand kit

`brands/<name>/brand.yaml` — `name`, `logo`, `logo_height` (px at 1080, default 56), `colors`
(`primary`, `text`, `muted`, `accent`, `accent_text`, `on_accent`, `line`, `panel`), `fonts`
(`headline`, `body`, `block`, `mono`), `tone`, `disclaimer`, `default_cta`. Paths resolve from
the repo root.

Fonts must be TTF/OTF: Pillow cannot read WOFF2. `brands/daridev/` ships the five static files
the system uses, with the OFL license, and `brands/daridev/README.md` documents the rules
(dark only, one accent, logo in the corner, no gradients, no invented numbers).

`campaigns/daridev-turismo.yaml` is the reference brief.

## Briefs

`campaigns/<name>.yaml` — required: `product`, `description`, `benefits` (3+), `offer`,
`audience`, `objective` (`awareness|conversion`), `url`. Optional: `layout`, `cta` (falls back
to the brand's `default_cta`), `kicker` (label above the statement, defaults to the brand name),
`brand`, `language` (`es`), `capture`/`captures`, `pain_points`, `differentiators`,
`objections`, `disclaimer`, `refs` (product photos, photo layout only), `concepts` (default 1,
max 6).

`on_image` takes exactly three statements, one per variant (A, B, C), and overrides the model's.
Use it to test three angles of the *same* pain: the model tends to drift to a benefit by the
second line. Each line is limited to 24 characters and the render fails if it does not fit.

Captures are expected to arrive already clean: the generator does not blur names, phones or
emails. Automating that invites publishing something half covered.

## Output

```
out/<campaign>/
  plan.json                     what the text model returned (concepts + A/B plan)
  finals/concept-1-4x5-A.png    1080x1350, per variant
  display/concept-1-1200x628-A.jpg   only when 16:9 is in the ratio set
  contact-sheet.png             the whole batch in one image, labelled
  copy.md                       on-image text vs platform headline, with character counts
  ab-plan.md                    waves, hypotheses, metrics, files that actually exist
  manifest.json                 layout, variants, capture, size, warnings, cost per asset
  clean/                        only in the photo layout: the art before the overlay
```

Sizes: `1:1` 1080×1080, `4:5` 1080×1350, `9:16` 1080×1920, `16:9` 1920×1080, `1.91:1`
1200×628 (Display, cropped from the 16:9 art).

Safe insets are per ratio, not one percentage: `9:16` reserves 250 px at the top and 320 px at
the bottom because Reels and TikTok cover them. In the photo layout the art is center-cropped
to the target aspect first, so a model that ignores `aspect_ratio` cannot distort the piece.

Product photos in `refs` are sent as `input_references` (downscaled to 1600 px, EXIF corrected,
max 14). The logo is composited by Pillow and never sent to the model.

## Cost

`type` and `capture` runs cost only the text call (≈ $0.004). The photo layout bills per image
(~$0.03–0.15 per 2K render), so a 3-concept × 2-ratio run is roughly **$0.2–0.9**.
`manifest.json` records the exact `usage.cost` per image; the budget guard uses a conservative
prior before the first call. Totals cover the current run only — cached art is not re-billed.

## Environment

| var | default | notes |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | required (text model, and images when the layout needs them) |
| `TEXT_MODEL` | `deepseek/deepseek-v4.1-flash` | copy + A/B plan |
| `IMAGE_MODEL` | `google/gemini-nano-banana-2.1` | photo layout only |
| `IMAGE_RESOLUTION` | `2K` | photo layout only |
| `OUTPUT_DIR` | `out` | |
| `MAX_COST_USD` | `5` | hard stop, checked before each image render |
| `CONCURRENCY` | `3` | parallel image calls |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | escape hatch |

### Changing the image model

Set `IMAGE_MODEL` and nothing else. At startup the script reads
`/api/v1/images/models/<model>/endpoints` and validates the requested aspect ratios and
resolution against what that model declares. If the new model can't do `4:5`, you get

```
error: aspect ratio(s) 4:5 not supported by this image model. Supported: 1:1, 16:9, ...
```

instead of a silent bad crop. Parameters the model doesn't declare are never sent. This check
runs only in the photo layout — the other two never touch the image API.

### Changing the text model

`TEXT_MODEL` is enough. The client prefers `response_format: json_schema` with
`provider.require_parameters`, falls back to `json_object`, and finally does one repair
turn. If a model is sloppy at Spanish, try `google/gemini-3.8-flash`.

## Tests

```bash
pytest -q
```

Covers per-ratio safe insets, statement geometry at three sizes, capture proportion, capture
selection under `--only`, a zero-cost type run, the `--variants` subset, brand token loading
and reference downscaling. No API key needed.
