"""Deterministic overlay: logo top-left, copy bottom-left, CTA pill bottom-right.

Everything is measured before drawing, so text never escapes the safe area and the
CTA never sits on top of the copy (it stacks under it when space runs out).
"""

from __future__ import annotations

import base64
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

SAFE = 0.05
LOGO_MAX_H = 0.11
HEAD_MAX = 0.060
HEAD_MIN = 0.024
SUB_MAX = 0.030
SUB_MIN = 0.018
CTA_H = 0.070
CTA_TEXT = 0.035
DISCLAIMER = 0.016
LINE = 1.18

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


def fit_block(text, font_path, max_w, max_h, start, floor, max_lines):
    """Shrink until the wrapped block fits, truncating with an ellipsis as last resort."""
    floor = max(int(floor), 10)
    size = max(int(start), floor)
    while size >= floor:
        font = load_font(font_path, size)
        lines = wrap(text, font, max_w)
        line_h = int(size * LINE)
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
    if len(lines) > max_lines or len(lines) * int(floor * LINE) > max_h:
        lines = lines[:max_lines]
        if lines:
            last = lines[-1]
            while last and font.getlength(last + "…") > max_w:
                last = last[:-1]
            lines[-1] = (last.rstrip() or "…") + "…"
        warnings.append(f"text truncated to fit safe area: {text[:40]!r}")
    return font, lines, int(floor * LINE), warnings


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


def _apply_scrim(img: Image.Image, top: int, bottom_alpha: int = 165) -> None:
    w, h = img.size
    top = max(0, min(top, h - 1))
    height = h - top
    ramp = Image.new("L", (1, height))
    for y in range(height):
        ramp.putpixel((0, y), int(bottom_alpha * (y / max(1, height - 1)) ** 0.85))
    gradient = ramp.resize((w, height))
    layer = Image.new("RGBA", (w, height), (0, 0, 0, 255))
    layer.putalpha(gradient)
    img.alpha_composite(layer, (0, top))


def _draw_lines(draw, xy, lines, font, line_h, fill, shadow_alpha: int = 130):
    x, y = xy
    offset = max(1, int(font.size * 0.025))
    for line in lines:
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


def compose(
    art_path,
    out_path,
    brand: dict,
    copy: dict,
    target_size: tuple[int, int] | None = None,
    crop_aspect: float | None = None,
    jpeg: bool = False,
) -> dict:
    """Draw logo/copy/CTA over the art. Returns layout info for the manifest and tests."""
    with Image.open(art_path) as source:
        art = source.convert("RGBA")
    if crop_aspect is None and target_size:
        crop_aspect = target_size[0] / target_size[1]
    if crop_aspect:
        art = _center_crop(art, crop_aspect)
    w, h = art.size
    safe = int(min(w, h) * SAFE)
    warnings: list[str] = []

    colors = brand.get("colors") or {}
    head_path = (brand.get("fonts") or {}).get("headline") or DEFAULT_HEAD_FONT
    body_path = (brand.get("fonts") or {}).get("body") or DEFAULT_BODY_FONT
    text_color = _color(colors.get("text", "#FFFFFF"))

    headline = (copy.get("headline") or "").strip()
    subhead = (copy.get("subhead") or "").strip()
    cta = (copy.get("cta") or "").strip()
    disclaimer = (copy.get("disclaimer") or brand.get("disclaimer") or "").strip()

    box_w = w - 2 * safe
    head_font, head_lines, head_lh, warn = fit_block(
        headline, head_path, box_w, h * 0.34, h * HEAD_MAX, h * HEAD_MIN, 3
    )
    warnings += warn
    if subhead:
        sub_font, sub_lines, sub_lh, warn = fit_block(
            subhead, body_path, box_w, h * 0.14, h * SUB_MAX, h * SUB_MIN, 2
        )
        warnings += warn
    else:
        sub_font, sub_lines, sub_lh = None, [], 0

    gap = int(h * 0.018)
    disc_font = load_font(body_path, max(int(h * DISCLAIMER), 10)) if disclaimer else None
    disc_wrapped = wrap(disclaimer, disc_font, box_w) if disc_font else []
    disc_lines = disc_wrapped[:2]
    if len(disc_wrapped) > 2:
        warnings.append("disclaimer truncated to 2 lines")
    disc_lh = int(disc_font.size * 1.2) if disc_font else 0
    disc_h = len(disc_lines) * disc_lh + (gap if disc_lines else 0)

    cta_font = load_font(head_path, max(int(h * CTA_TEXT), 10))
    pill_h = int(h * CTA_H)
    pad = int(pill_h * 0.6)
    pill_w = int(min(cta_font.getlength(cta) + 2 * pad, w * 0.72)) if cta else 0

    bottom = h - safe - disc_h
    block_h = len(head_lines) * head_lh + (gap + len(sub_lines) * sub_lh if sub_lines else 0)
    text_top = bottom - block_h
    measured = [head_font.getlength(line) for line in head_lines]
    if sub_lines:
        measured += [sub_font.getlength(line) for line in sub_lines]
    text_w = min(max(measured or [0]), box_w)
    text_rect = (safe, text_top, safe + int(text_w), bottom)

    cta_rect = None
    if cta:
        cta_rect = (w - safe - pill_w, bottom - pill_h, w - safe, bottom)
        if overlaps(text_rect, cta_rect, gap):
            # No room bottom-right: pin the pill to the bottom-left and lift the copy above it.
            cta_rect = (safe, bottom - pill_h, safe + pill_w, bottom)
            text_top = cta_rect[1] - gap - block_h
            text_rect = (safe, text_top, safe + int(text_w), text_top + block_h)

    disc_rect = None
    if disc_lines:
        disc_top = h - safe - len(disc_lines) * disc_lh
        disc_rect = (
            safe,
            disc_top,
            safe + int(min(max(disc_font.getlength(line) for line in disc_lines), box_w)),
            h - safe,
        )

    tops = [text_top]
    if cta_rect:
        tops.append(cta_rect[1])
    if disc_rect:
        tops.append(disc_rect[1])
    _apply_scrim(art, min(tops) - int(h * 0.04))

    draw = ImageDraw.Draw(art)
    logo_rect = None
    logo_path = brand.get("logo_path")
    if logo_path and Path(logo_path).exists():
        with Image.open(logo_path) as source:
            logo = source.convert("RGBA")
        logo_h = int(h * LOGO_MAX_H)
        logo_w = max(1, int(logo.width * logo_h / logo.height))
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
        art.alpha_composite(logo, (safe, safe))
        logo_rect = (safe, safe, safe + logo_w, safe + logo_h)
    elif logo_path:
        warnings.append(f"logo not found: {logo_path}")

    y = text_top
    _draw_lines(draw, (safe, y), head_lines, head_font, head_lh, text_color)
    y += len(head_lines) * head_lh
    if sub_lines:
        y += gap
        _draw_lines(draw, (safe, y), sub_lines, sub_font, sub_lh, _color(colors.get("text", "#FFFFFF"), 235))

    if cta_rect:
        draw.rounded_rectangle(
            cta_rect, radius=pill_h // 2, fill=_color(colors.get("accent", "#FFFFFF"))
        )
        draw.text(
            ((cta_rect[0] + cta_rect[2]) / 2, (cta_rect[1] + cta_rect[3]) / 2),
            cta,
            font=cta_font,
            fill=_color(colors.get("on_accent", "#000000")),
            anchor="mm",
        )

    if disc_rect:
        _draw_lines(
            draw,
            (safe, disc_rect[1]),
            disc_lines,
            disc_font,
            disc_lh,
            _color(colors.get("text", "#FFFFFF"), 200),
            shadow_alpha=110,
        )

    output = art.resize(target_size, Image.LANCZOS) if target_size else art
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if jpeg or out_path.suffix.lower() in {".jpg", ".jpeg"}:
        output.convert("RGB").save(out_path, "JPEG", quality=88, optimize=True)
    else:
        output.save(out_path, "PNG")

    return {
        "render_size": (w, h),
        "output_size": output.size,
        "rects": {"logo": logo_rect, "text": text_rect, "cta": cta_rect, "disclaimer": disc_rect},
        "warnings": warnings,
    }
