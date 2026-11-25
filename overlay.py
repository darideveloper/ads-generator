"""Deterministic composition.

Three layouts, one grammar:

- ``compose_type``    brand canvas + kicker + statement + CTA. No image, no cost.
- ``compose_capture`` the same statement plus a real screenshot in a framed slot.
- ``compose``         photo with the copy washed over it (legacy overlay, now on brand).

Every text block is measured before it is drawn, so nothing escapes the safe box and
the CTA never sits on top of the copy.
"""

from __future__ import annotations

import base64
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# Safe area, in px on a 1080 canvas. ``9:16`` keeps the copy out of the platform chrome:
# Reels/TikTok eat ~250 px at the top and ~320 px at the bottom.
SAFE_INSETS = {
    "1:1": {"top": 72, "bottom": 72, "left": 72, "right": 72},
    "4:5": {"top": 72, "bottom": 72, "left": 72, "right": 72},
    "9:16": {"top": 250, "bottom": 320, "left": 72, "right": 72},
    "16:9": {"top": 90, "bottom": 90, "left": 72, "right": 72},
    "1.91:1": {"top": 48, "bottom": 48, "left": 48, "right": 48},
}
RATIO_ASPECT = {"1:1": 1.0, "4:5": 0.8, "9:16": 9 / 16, "16:9": 16 / 9, "1.91:1": 1.91}

SAFE = 0.05  # uniform fallback for any size that matches no known ratio
LOGO_HEIGHT = 56
KICKER = 24
HEAD_FILL = 240  # upper bound for a statement headline; it shrinks to fit the copy
HEAD_PHOTO = 0.060
HEAD_MIN = 0.024
SUB_MAX = 0.030
SUB_MIN = 0.018
CTA = 40
CTA_H = 0.070
CTA_TEXT = 0.035
DISCLAIMER = 0.016
LINE = 1.18
GRID = 44
GRID_ALPHA = 23  # 9 % ice
WASH_ALPHA = 205

DEFAULT_HEAD_FONT = "/usr/share/fonts/truetype/litefonts/Roboto-Bold.ttf"
DEFAULT_BODY_FONT = "/usr/share/fonts/truetype/litefonts/Roboto-Regular.ttf"


@lru_cache(maxsize=64)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), int(size))


def _color(value: str | None, alpha: int = 255) -> tuple[int, int, int, int]:
    value = (value or "#FFFFFF").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)
    except (ValueError, IndexError):
        return (255, 255, 255, alpha)


def scale_for(size) -> float:
    return max(0.8, min(1.05, min(size) / 1080))


def ratio_key(size) -> str | None:
    aspect = size[0] / size[1]
    for key, target in RATIO_ASPECT.items():
        if abs(aspect - target) < 0.01:
            return key
    return None


def insets_for(size) -> dict:
    """Safe insets in px for this canvas, scaled from the 1080 reference.

    Type scales are clamped (0.8-1.05) so headlines stay readable; margins are not,
    because a 2048 px photo needs a proportionally larger margin than a 1080 px one.
    """
    scale = max(0.8, min(size) / 1080)
    key = ratio_key(size)
    spec = SAFE_INSETS.get(key) if key else None
    if not spec:
        pad = int(min(size) * SAFE)
        return {"top": pad, "bottom": pad, "left": pad, "right": pad}
    return {side: int(value * scale) for side, value in spec.items()}


def wrap(text: str, font, max_w: float) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines = [words[0]]
    for word in words[1:]:
        candidate = f"{lines[-1]} {word}"
        if font.getlength(candidate) <= max_w:
            lines[-1] = candidate
        else:
            lines.append(word)

    # Hard-break single tokens wider than the box (URLs, long product names).
    broken: list[str] = []
    for line in lines:
        if font.getlength(line) <= max_w:
            broken.append(line)
            continue
        chunk = ""
        for char in line:
            if font.getlength(chunk + char) <= max_w:
                chunk += char
            else:
                if chunk:
                    broken.append(chunk)
                chunk = char
        if chunk:
            broken.append(chunk)
    return broken


def fit_block(
    text,
    font_path,
    max_w,
    max_h,
    start,
    floor,
    max_lines,
    line_ratio: float = LINE,
    hard_break: bool = True,
):
    """Largest size that fits, from ``start`` down to ``floor``.

    With ``hard_break=False`` a word that alone exceeds the width is not broken mid-word;
    the block keeps shrinking instead (a split word is worse than a smaller headline).
    """
    floor = max(int(floor), 10)
    size = max(int(start), floor)
    words = (text or "").split()
    while size >= floor:
        font = load_font(font_path, size)
        if not hard_break and words and max(font.getlength(word) for word in words) > max_w:
            size -= 2
            continue
        lines = wrap(text, font, max_w)
        line_h = int(size * line_ratio)
        if (
            len(lines) <= max_lines
            and len(lines) * line_h <= max_h
            and all(font.getlength(line) <= max_w for line in lines)
        ):
            return font, lines, line_h, []
        size -= 2
    font = load_font(font_path, floor)
    lines = wrap(text, font, max_w)
    warnings = []
    if len(lines) > max_lines or len(lines) * int(floor * line_ratio) > max_h:
        lines = lines[:max_lines]
        if lines:
            last = lines[-1]
            while last and font.getlength(last + "…") > max_w:
                last = last[:-1]
            lines[-1] = (last.rstrip() or "…") + "…"
        warnings.append(f"text truncated to fit safe area: {text[:40]!r}")
    return font, lines, int(floor * line_ratio), warnings


def overlaps(a, b, pad: int = 0) -> bool:
    return not (
        a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1]
    )


def _center_crop(img: Image.Image, aspect: float) -> Image.Image:
    w, h = img.size
    if w / h > aspect:
        new_w = int(h * aspect)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = int(w / aspect)
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


def _apply_wash(img: Image.Image, top: int, alpha: int = WASH_ALPHA) -> None:
    """Flat carbon wash. The system forbids gradients, so this is one hard-edged panel."""
    w, h = img.size
    top = max(0, min(top, h))
    if h - top <= 0:
        return
    panel = Image.new("RGBA", (w, h - top), _color("#0b0e11", alpha))
    img.alpha_composite(panel, (0, top))


def _draw_lines(draw, xy, lines, font, line_h, fill, shadow_alpha: int = 0):
    x, y = xy
    offset = max(1, int(font.size * 0.025))
    for line in lines:
        if shadow_alpha:
            draw.text((x + offset, y + offset), line, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h


def ref_data_url(path: str | Path, max_edge: int = 1600) -> tuple[str, int]:
    """Reference image as a data URL, downscaled and EXIF-corrected so requests stay small."""
    with Image.open(path) as source:
        img = ImageOps.exif_transpose(source)
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buffer = io.BytesIO()
        has_alpha = img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        )
        if has_alpha:
            img.convert("RGBA").save(buffer, "PNG", optimize=True)
            mime = "image/png"
        else:
            img.convert("RGB").save(buffer, "JPEG", quality=90)
            mime = "image/jpeg"
    raw = buffer.getvalue()
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}", len(raw)


# ----------------------------------------------------------------- brand canvas


def _tokens(brand: dict) -> dict:
    colors = brand.get("colors") or {}
    fonts = brand.get("fonts") or {}
    return {
        "paper": _color(colors.get("primary", "#0b0e11")),
        "ink": _color(colors.get("text", "#FFFFFF")),
        "muted": _color(colors.get("muted") or colors.get("text", "#FFFFFF"), 220),
        "accent": _color(colors.get("accent", "#67e8f9")),
        "accent_text": _color(colors.get("accent_text") or colors.get("accent", "#67e8f9")),
        "on_accent": _color(colors.get("on_accent", "#083344")),
        "line": _color(colors.get("line") or colors.get("accent", "#67e8f9")),
        "display": fonts.get("headline") or DEFAULT_HEAD_FONT,
        "body": fonts.get("body") or DEFAULT_BODY_FONT,
        "block": fonts.get("block") or fonts.get("headline") or DEFAULT_HEAD_FONT,
        "mono": fonts.get("mono") or fonts.get("body") or DEFAULT_BODY_FONT,
    }


def _canvas(size, brand: dict) -> Image.Image:
    """Paper + ice grid. The grid is the only decoration allowed in the background."""
    tokens = _tokens(brand)
    canvas = Image.new("RGBA", size, tokens["paper"])
    step = int(GRID * scale_for(size))
    if step < 8:
        return canvas
    grid = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(grid)
    tint = tokens["accent"][:3] + (GRID_ALPHA,)
    for x in range(step, size[0], step):
        draw.line([(x, 0), (x, size[1])], fill=tint, width=1)
    for y in range(step, size[1], step):
        draw.line([(0, y), (size[0], y)], fill=tint, width=1)
    canvas.alpha_composite(grid)
    return canvas


def _frame(canvas: Image.Image, brand: dict, insets: dict) -> None:
    tokens = _tokens(brand)
    w, h = canvas.size
    width = max(2, int(round(2 * scale_for(canvas.size))))
    ImageDraw.Draw(canvas).rectangle(
        (insets["left"], insets["top"], w - 1 - insets["right"], h - 1 - insets["bottom"]),
        outline=tokens["line"],
        width=width,
    )


def _logo(canvas: Image.Image, brand: dict, insets: dict):
    logo_path = brand.get("logo_path")
    if not logo_path or not Path(logo_path).exists():
        return None
    target_h = int((brand.get("logo_height") or LOGO_HEIGHT) * scale_for(canvas.size))
    with Image.open(logo_path) as source:
        logo = source.convert("RGBA")
    logo_w = max(1, int(logo.width * target_h / logo.height))
    logo = logo.resize((logo_w, target_h), Image.LANCZOS)
    canvas.alpha_composite(logo, (insets["left"], insets["top"]))
    return (insets["left"], insets["top"], insets["left"] + logo_w, insets["top"] + target_h)


def _draw_tracked(draw, xy, text, font, fill, track) -> float:
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += font.getlength(char) + track
    return x - track


def _tracked_width(text, font, track) -> float:
    if not text:
        return 0.0
    return sum(font.getlength(char) for char in text) + track * (len(text) - 1)


def _statement(canvas: Image.Image, brand: dict, copy: dict, insets: dict,
               kicker: str = "") -> dict:
    """Bottom-anchored kicker / rule / headline / CTA stack. Measures before drawing."""
    w, h = canvas.size
    scale = scale_for(canvas.size)
    tokens = _tokens(brand)
    warnings: list[str] = []

    left, right = insets["left"], w - insets["right"]
    box_w = right - left
    bottom = h - insets["bottom"]
    top_limit = insets["top"]

    headline = " ".join((copy.get("headline") or "").split())
    cta = " ".join((copy.get("cta") or "").split())
    kicker = " ".join((kicker or "").split()).upper()

    # CTA block — a bounded rectangle, never a pill.
    cta_font = None
    cta_w = cta_h = 0
    if cta:
        size = int(CTA * scale)
        while size > 16 and load_font(tokens["block"], size).getlength(cta) > box_w * 0.82:
            size -= 2
        cta_font = load_font(tokens["block"], size)
        pad_x, pad_y = int(size * 0.9), int(size * 0.6)
        cta_w = int(cta_font.getlength(cta)) + 2 * pad_x
        cta_h = size + 2 * pad_y

    kicker_size = int(KICKER * scale)
    kicker_font = load_font(tokens["mono"], kicker_size) if kicker else None
    kicker_track = kicker_size * 0.14
    kicker_h = int(kicker_size * 1.15) if kicker else 0
    rule_h = max(2, int(round(4 * scale)))
    rule_w = int(120 * scale)

    gap_rule = int(12 * scale)  # kicker → rule
    gap_head = int(24 * scale)  # rule → headline
    gap_cta = int(32 * scale)  # headline → CTA

    fixed = (kicker_h + gap_rule + rule_h + gap_head + gap_cta + cta_h) if kicker else (
        rule_h + gap_head + gap_cta + cta_h
    )
    reserve = bottom - fixed - top_limit
    floor = int(48 * scale)
    max_lines = 4 if h / w > 1.6 else 3
    head_font, head_lines, head_lh, warn = fit_block(
        headline,
        tokens["display"],
        int(box_w * 0.96),  # keep the last glyph off the frame
        max(int(reserve), floor * 2),
        int(HEAD_FILL * scale),
        floor,
        max_lines,
        line_ratio=0.98,
        hard_break=False,
    )
    warnings += warn

    # Display type is set at 0.98 line height, so the last line's descender hangs below the
    # block. Measure it from the font instead of guessing a magic number.
    ascent, descent = head_font.getmetrics()
    overflow = max(0, ascent + descent - head_lh)
    head_h = len(head_lines) * head_lh + overflow
    cta_top = bottom - cta_h
    head_bottom = cta_top - gap_cta
    head_top = head_bottom - head_h
    rule_top = head_top - gap_head - rule_h
    kicker_top = rule_top - gap_rule - kicker_h if kicker else rule_top
    if kicker_top < top_limit:
        warnings.append("statement overflows the safe area")
        kicker_top = top_limit

    draw = ImageDraw.Draw(canvas)
    if kicker:
        _draw_tracked(
            draw, (left, kicker_top), kicker, kicker_font, tokens["accent_text"], kicker_track
        )
    draw.rectangle((left, rule_top, left + rule_w, rule_top + rule_h), fill=tokens["accent"])
    _draw_lines(draw, (left, head_top), head_lines, head_font, head_lh, tokens["ink"])

    cta_rect = None
    if cta:
        cta_rect = (left, cta_top, left + cta_w, cta_top + cta_h)
        draw.rectangle(cta_rect, fill=tokens["accent"])
        draw.text(
            ((cta_rect[0] + cta_rect[2]) / 2, (cta_rect[1] + cta_rect[3]) / 2),
            cta,
            font=cta_font,
            fill=tokens["on_accent"],
            anchor="mm",
        )

    text_w = int(max((head_font.getlength(line) for line in head_lines), default=0))
    return {
        "rects": {
            "kicker": (left, kicker_top, left + int(_tracked_width(kicker, kicker_font, kicker_track)), kicker_top + kicker_h) if kicker else None,
            "rule": (left, rule_top, left + rule_w, rule_top + rule_h),
            "text": (left, head_top, left + text_w, head_bottom),
            "cta": cta_rect,
        },
        "top": kicker_top,
        "warnings": warnings,
    }


# --------------------------------------------------------------------- layouts


def compose_type(out_path, brand: dict, copy: dict, target_size, kicker: str = "",
                 jpeg: bool = False) -> dict:
    """Typographic statement. Zero images, zero model calls, zero cost."""
    canvas = _canvas(target_size, brand)
    insets = insets_for(target_size)
    _frame(canvas, brand, insets)
    logo_rect = _logo(canvas, brand, insets)
    info = _statement(canvas, brand, copy, insets, kicker)
    _save(canvas, out_path, jpeg)
    return {
        "render_size": tuple(canvas.size),
        "output_size": tuple(canvas.size),
        "rects": {"logo": logo_rect, **info["rects"]},
        "warnings": info["warnings"],
    }


def compose_capture(out_path, brand: dict, copy: dict, target_size, capture_path,
                    kicker: str = "", jpeg: bool = False) -> dict:
    """Feathered statement plus a real screenshot in a registration-framed slot."""
    canvas = _canvas(target_size, brand)
    insets = insets_for(target_size)
    _frame(canvas, brand, insets)
    logo_rect = _logo(canvas, brand, insets)
    info = _statement(canvas, brand, copy, insets, kicker)

    w, h = canvas.size
    scale = scale_for(canvas.size)
    gap = int(28 * scale)
    slot_top = (logo_rect[3] if logo_rect else insets["top"]) + gap
    slot = (insets["left"], slot_top, w - insets["right"], info["top"] - gap)
    capture_rect = _place_capture(canvas, capture_path, slot, brand, info["warnings"])
    _save(canvas, out_path, jpeg)
    return {
        "render_size": (w, h),
        "output_size": (w, h),
        "rects": {"logo": logo_rect, "capture": capture_rect, **info["rects"]},
        "slot": slot,
        "warnings": info["warnings"],
    }


def _place_capture(canvas, path, slot, brand: dict, warnings: list) -> tuple | None:
    """Fit the whole screenshot inside the slot, never cropped, never deformed, never upscaled.

    Cropping a real UI to a different aspect cuts the columns that carry the numbers, so the
    capture keeps its own proportion and the brand canvas absorbs the difference.
    """
    x0, y0, x1, y1 = slot
    slot_w, slot_h = x1 - x0, y1 - y0
    if slot_w < 40 or slot_h < 40:
        warnings.append("no room left for the capture")
        return None
    try:
        with Image.open(path) as source:
            shot = ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, ValueError) as exc:
        warnings.append(f"capture could not be read: {path} ({exc})")
        return None

    factor = min(slot_w / shot.width, slot_h / shot.height)
    if factor < 1:
        shot = shot.resize(
            (max(1, int(shot.width * factor)), max(1, int(shot.height * factor))), Image.LANCZOS
        )
    elif shot.width < slot_w * 0.9:
        warnings.append(f"capture smaller than the slot: {shot.width}px wide < {slot_w}px")

    x = x0 + (slot_w - shot.width) // 2
    y = y0 + (slot_h - shot.height) // 2
    border = max(2, int(round(2 * scale_for(canvas.size))))
    ImageDraw.Draw(canvas).rectangle(
        (x - border, y - border, x + shot.width + border, y + shot.height + border),
        outline=_tokens(brand)["line"],
        width=border,
    )
    canvas.alpha_composite(shot, (x, y))
    return (x, y, x + shot.width, y + shot.height)


def compose(
    art_path,
    out_path,
    brand: dict,
    copy: dict,
    target_size: tuple[int, int] | None = None,
    crop_aspect: float | None = None,
    jpeg: bool = False,
) -> dict:
    """Photo layout: the art carries the message, the copy sits on a flat carbon panel."""
    with Image.open(art_path) as source:
        art = source.convert("RGBA")
    if crop_aspect is None and target_size:
        crop_aspect = target_size[0] / target_size[1]
    if crop_aspect:
        art = _center_crop(art, crop_aspect)
    w, h = art.size
    insets = insets_for((w, h))
    left, right = insets["left"], w - insets["right"]
    warnings: list[str] = []

    tokens = _tokens(brand)

    headline = (copy.get("headline") or "").strip()
    subhead = (copy.get("subhead") or "").strip()
    cta = (copy.get("cta") or "").strip()
    disclaimer = (copy.get("disclaimer") or brand.get("disclaimer") or "").strip()

    box_w = right - left
    head_font, head_lines, head_lh, warn = fit_block(
        headline, tokens["display"], box_w, h * 0.34, h * HEAD_PHOTO, h * HEAD_MIN, 3
    )
    warnings += warn
    if subhead:
        sub_font, sub_lines, sub_lh, warn = fit_block(
            subhead, tokens["body"], box_w, h * 0.14, h * SUB_MAX, h * SUB_MIN, 2
        )
        warnings += warn
    else:
        sub_font, sub_lines, sub_lh = None, [], 0

    gap = int(h * 0.028)
    disc_font = load_font(tokens["body"], max(int(h * DISCLAIMER), 10)) if disclaimer else None
    disc_wrapped = wrap(disclaimer, disc_font, box_w) if disc_font else []
    disc_lines = disc_wrapped[:2]
    if len(disc_wrapped) > 2:
        warnings.append("disclaimer truncated to 2 lines")
    disc_lh = int(disc_font.size * 1.2) if disc_font else 0
    disc_h = len(disc_lines) * disc_lh + (gap if disc_lines else 0)

    cta_font = load_font(tokens["block"], max(int(h * CTA_TEXT), 10))
    pill_h = int(h * CTA_H)
    pad = int(pill_h * 0.6)
    pill_w = int(min(cta_font.getlength(cta) + 2 * pad, box_w * 0.9)) if cta else 0

    bottom = h - insets["bottom"] - disc_h
    block_h = len(head_lines) * head_lh + (gap + len(sub_lines) * sub_lh if sub_lines else 0)
    text_top = bottom - block_h
    measured = [head_font.getlength(line) for line in head_lines]
    if sub_lines:
        measured += [sub_font.getlength(line) for line in sub_lines]
    text_w = min(max(measured or [0]), box_w)
    text_rect = (left, text_top, left + int(text_w), bottom)

    cta_rect = None
    if cta:
        cta_rect = (right - pill_w, bottom - pill_h, right, bottom)
        if overlaps(text_rect, cta_rect, gap):
            cta_rect = (left, bottom - pill_h, left + pill_w, bottom)
            text_top = cta_rect[1] - gap - block_h
            text_rect = (left, text_top, left + int(text_w), text_top + block_h)

    disc_rect = None
    if disc_lines:
        disc_top = h - insets["bottom"] - len(disc_lines) * disc_lh
        disc_rect = (
            left,
            disc_top,
            left + int(min(max(disc_font.getlength(line) for line in disc_lines), box_w)),
            h - insets["bottom"],
        )

    tops = [text_top] + ([cta_rect[1]] if cta_rect else []) + ([disc_rect[1]] if disc_rect else [])
    _apply_wash(art, min(tops) - int(h * 0.05))

    draw = ImageDraw.Draw(art)
    logo_rect = _logo(art, brand, insets)

    y = text_top
    _draw_lines(draw, (left, y), head_lines, head_font, head_lh, tokens["ink"], shadow_alpha=0)
    y += len(head_lines) * head_lh
    if sub_lines:
        y += gap
        _draw_lines(draw, (left, y), sub_lines, sub_font, sub_lh, tokens["muted"])

    if cta_rect:
        draw.rectangle(cta_rect, fill=tokens["accent"])
        draw.text(
            ((cta_rect[0] + cta_rect[2]) / 2, (cta_rect[1] + cta_rect[3]) / 2),
            cta,
            font=cta_font,
            fill=tokens["on_accent"],
            anchor="mm",
        )

    if disc_rect:
        _draw_lines(draw, (left, disc_rect[1]), disc_lines, disc_font, disc_lh, tokens["muted"])

    output = art.resize(target_size, Image.LANCZOS) if target_size else art
    _save(output, out_path, jpeg)

    return {
        "render_size": (w, h),
        "output_size": tuple(output.size),
        "rects": {"logo": logo_rect, "text": text_rect, "cta": cta_rect, "disclaimer": disc_rect},
        "warnings": warnings,
    }


def _save(image: Image.Image, out_path, jpeg: bool = False) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if jpeg or out_path.suffix.lower() in {".jpg", ".jpeg"}:
        image.convert("RGB").save(out_path, "JPEG", quality=90, optimize=True)
    else:
        image.save(out_path, "PNG", optimize=True)


# ---------------------------------------------------------------- contact sheet


def contact_sheet(entries, out_path, brand: dict, cell: int = 360, cols: int = 4) -> dict:
    """One image to review a whole batch: thumbnails plus ``concept · ratio · variant``."""
    entries = [(Path(path), label) for path, label in entries if Path(path).exists()]
    if not entries:
        return {"output_size": (0, 0)}
    tokens = _tokens(brand)
    cols = max(1, min(cols, len(entries)))
    rows = -(-len(entries) // cols)
    label_h = int(cell * 0.13)
    gap = int(cell * 0.08)
    margin = gap
    sheet_w = margin * 2 + cols * cell + (cols - 1) * gap
    sheet_h = margin * 2 + rows * (cell + label_h) + (rows - 1) * gap
    sheet = Image.new("RGBA", (sheet_w, sheet_h), tokens["paper"])
    draw = ImageDraw.Draw(sheet)
    font = load_font(tokens["mono"], max(14, int(label_h * 0.52)))

    for index, (path, label) in enumerate(entries):
        row, col = divmod(index, cols)
        x = margin + col * (cell + gap)
        y = margin + row * (cell + label_h + gap)
        with Image.open(path) as source:
            thumb = source.convert("RGBA")
        thumb.thumbnail((cell, cell), Image.LANCZOS)
        sheet.alpha_composite(thumb, (x + (cell - thumb.width) // 2, y + (cell - thumb.height) // 2))
        draw.rectangle((x, y, x + cell, y + cell), outline=tokens["line"], width=2)
        draw.text(
            (x + cell / 2, y + cell + label_h * 0.18),
            label,
            font=font,
            fill=tokens["muted"],
            anchor="ma",
        )
    _save(sheet, out_path)
    return {"output_size": sheet.size}
