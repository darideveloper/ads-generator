# DariDev brand kit

One brand, many campaigns. Mirrors the SB4 Blueprint Ice system used in video and stills
(`content-os/apps/hyperframes/assets/tokens.css`, `content-os/apps/images/DESIGN.md`).

## Rules that are not negotiable

- **Dark only.** `paper` is the canvas. There is no light variant.
- **One accent.** Ice cyan appears once per piece: the rule, the CTA block, or the grid — never two.
- **Logo in the corner.** 56 px on a 1080 canvas, top-left, inside the safe box. Never centred, never scaled up.
- **No gradients.** Flat fills only, except the two grid axes.
- **No pure black.** Use `paper`.
- **Numbers come from real data or they do not appear.** No invented percentages, clients or timelines.

## Files

| Path | What it is |
|---|---|
| `brand.yaml` | Tokens, fonts by role, tone, default CTA |
| `fonts/` | Static TTF (OFL 1.1, license text included). Pillow cannot read the WOFF2 used on the web |
| `assets/logo-mark.png` | White symbol, 800×800, transparent. For dark backgrounds only |

## Why the fonts are static TTF

The web kit ships WOFF2, which Pillow cannot open. These are the same families, instanced
from the official variable fonts at the weights the system uses:

| File | Family | Weight |
|---|---|---|
| `JetBrainsMono-ExtraBold.ttf` | JetBrains Mono | 800 |
| `ArchivoBlack-Regular.ttf` | Archivo Black | 400 |
| `Inter-Regular.ttf` | Inter | 400 |
| `Inter-Bold.ttf` | Inter | 700 |
| `SourceCodePro-Bold.ttf` | Source Code Pro | 700 |
