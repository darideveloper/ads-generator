from PIL import Image

import overlay

BRAND = {
    "name": "Acme",
    "logo_path": None,
    "colors": {"text": "#FFFFFF", "accent": "#22D3EE", "on_accent": "#04121A"},
    "fonts": {"headline": overlay.DEFAULT_HEAD_FONT, "body": overlay.DEFAULT_BODY_FONT},
    "disclaimer": "",
}

COPY = {
    "headline": "Ahorra 20% en tu café de cada día con envío gratis",
    "subhead": "Oferta válida solo esta semana para nuevos clientes",
    "cta": "Comprar ahora",
    "disclaimer": "Aplican términos y condiciones.",
}


def make_art(tmp_path, size=(2048, 2560)):
    path = tmp_path / "art.png"
    Image.new("RGB", size, (30, 45, 70)).save(path)
    return path


def test_long_copy_stays_inside_safe_area(tmp_path):
    info = overlay.compose(
        make_art(tmp_path), tmp_path / "out.png", BRAND, COPY, target_size=(1080, 1350)
    )
    width, height = info["render_size"]
    safe = int(min(width, height) * overlay.SAFE)
    for key in ("text", "cta", "disclaimer"):
        rect = info["rects"][key]
        assert rect, key
        assert rect[0] >= safe - 1, (key, rect)
        assert rect[2] <= width - safe + 1, (key, rect)
        assert rect[1] >= safe - 1, (key, rect)
        assert rect[3] <= height - safe + 1, (key, rect)
    assert Image.open(tmp_path / "out.png").size == (1080, 1350)


def test_cta_never_overlaps_copy_on_narrow_art(tmp_path):
    info = overlay.compose(
        make_art(tmp_path, (2048, 3641)), tmp_path / "story.png", BRAND, COPY, target_size=(1080, 1920)
    )
    assert not overlay.overlaps(info["rects"]["text"], info["rects"]["cta"])
    assert not overlay.overlaps(info["rects"]["text"], info["rects"]["disclaimer"])


def test_display_crop_size(tmp_path):
    # Square art must be center-cropped to 1.91:1, never squashed.
    info = overlay.compose(
        make_art(tmp_path, (2048, 2048)),
        tmp_path / "display.jpg",
        BRAND,
        COPY,
        target_size=(1200, 628),
        jpeg=True,
    )
    assert Image.open(tmp_path / "display.jpg").size == (1200, 628)
    assert info["output_size"] == (1200, 628)


def test_unbreakable_token_is_broken(tmp_path):
    copy = dict(COPY, headline="Supercalifragilisticoespialidoso" * 2, subhead="")
    info = overlay.compose(
        make_art(tmp_path), tmp_path / "long.png", BRAND, copy, target_size=(1080, 1350)
    )
    width, _ = info["render_size"]
    safe = int(min(width, info["render_size"][1]) * overlay.SAFE)
    assert info["rects"]["text"][2] <= width - safe + 1
    assert Image.open(tmp_path / "long.png").size == (1080, 1350)


def test_long_disclaimer_wraps_to_two_lines(tmp_path):
    copy = dict(
        COPY,
        disclaimer=(
            "Aplican términos y condiciones. Válido solo en compras realizadas antes "
            "del 31 de diciembre de 2026 en la tienda online."
        ),
    )
    info = overlay.compose(make_art(tmp_path), tmp_path / "legal.png", BRAND, copy)
    assert info["rects"]["disclaimer"]
    safe = int(min(info["render_size"]) * overlay.SAFE)
    assert info["rects"]["disclaimer"][1] >= safe - 1
    assert info["rects"]["disclaimer"][3] <= info["render_size"][1] - safe + 1


def test_ref_data_url_downscales(tmp_path):
    path = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (200, 10, 10)).save(path)
    url, size = overlay.ref_data_url(path, max_edge=1600)
    assert url.startswith("data:image/jpeg;base64,")
    assert size < 4000 * 3000
