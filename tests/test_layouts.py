"""Layout engine: brand canvas, statement, capture slot, exports."""

import json

import pytest
import yaml
from PIL import Image

import ads
import overlay

BRAND_PATH = ads.ROOT / "brands" / "daridev" / "brand.yaml"

CONCEPTS = [
    {
        "angle": "problem",
        "hook": "Todo vive en WhatsApp",
        "headlines": ["Deja de operar por WhatsApp", "Una sola pantalla", "El día completo"],
        "on_image": ["Tu día en una pantalla", "Sin Excel", "Nadie más sobrevende"],
        "subhead": "Reservas, unidades y pasajeros en el mismo lugar",
        "primary_text": "Registra la reserva una vez y el resto se ordena solo.",
        "headline_short": "Panel de operación",
        "description": "Hecho para operar",
        "cta": "Escríbeme",
        "image_prompt": "Tidy desk with a laptop at dawn.",
    },
    {
        "angle": "proof",
        "hook": "Así se ve el lunes",
        "headlines": ["Cuatro años operando", "Sin hojas de cálculo", "Todo en un panel"],
        "on_image": ["Así se ve el lunes", "Cero Excel", "Todo en un panel"],
        "subhead": "Lo mismo que ya usan tour operadores en Cancún",
        "primary_text": "Un panel que ya opera con reservas reales todos los días.",
        "headline_short": "Operación real",
        "description": "Casos reales",
        "cta": "Ver más",
        "image_prompt": "Tour operator office in the morning.",
    },
]

AB_PLAN = {
    "primary_metric": "ctr",
    "guardrails": ["cpa"],
    "tests": [
        {
            "title": "On-image A vs B",
            "concept_index": 1,
            "variants": ["A", "B", "C"],
            "hypothesis": "Menos palabras gana",
            "decision_rule": "95 % de confianza",
            "min_sample": "4.000 impresiones",
        }
    ],
}


def campaign(tmp_path, layout, captures=None, concepts=1):
    body = (
        "brand: daridev\n"
        f"layout: {layout}\n"
        "product: Panel de operación turística\n"
        "description: Un panel para registrar reservas y asignar unidades.\n"
        'benefits: ["Reservas en un solo lugar", "Asignación sin Excel", "Confirmación sola"]\n'
        "offer: Diagnóstico inicial sin costo\n"
        "audience: Dueños de tour operadores en Cancún\n"
        "objective: conversion\n"
        "url: https://example.com/turismo\n"
        f"concepts: {concepts}\n"
    )
    if captures:
        body += "captures:\n" + "".join(f"  - {path}\n" for path in captures)
    path = tmp_path / f"camp-{layout}.yaml"
    path.write_text(body)
    return path


def capture_file(tmp_path, name, size=(1600, 900)):
    path = tmp_path / name
    Image.new("RGB", size, (40, 70, 90)).save(path)
    return path


def stub(monkeypatch, tmp_path, count=1):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(
        ads.openrouter,
        "chat_json",
        lambda *a, **k: ({"concepts": CONCEPTS[:count], "ab_plan": AB_PLAN}, 0.001),
    )

    def no_image(*a, **k):
        raise AssertionError("this layout must not call the image model")

    monkeypatch.setattr(ads.openrouter, "image_capabilities", no_image)
    monkeypatch.setattr(ads.openrouter, "generate_image", no_image)


# ------------------------------------------------------------------ brand kit


def test_brand_tokens_load():
    brand = ads.load_brand(BRAND_PATH)
    assert brand["logo_height"] == 56
    for key in ("primary", "text", "muted", "accent", "accent_text", "on_accent", "line", "panel"):
        assert brand["colors"][key].startswith("#"), key
    for role in ("headline", "body", "block", "mono"):
        assert brand["fonts"][role].endswith(".ttf"), role
        overlay.load_font(brand["fonts"][role], 40)
    assert brand["default_cta"]


# --------------------------------------------------------------- safe area


def test_story_insets_clear_the_platform_chrome():
    insets = overlay.insets_for((1080, 1920))
    assert insets["top"] >= 250
    assert insets["bottom"] >= 320
    assert insets["left"] == 72


def test_type_layout_stays_inside_insets(tmp_path):
    brand = ads.load_brand(BRAND_PATH)
    for size in ((1080, 1350), (1080, 1920), (1080, 1080)):
        info = overlay.compose_type(
            tmp_path / f"type-{size[1]}.png",
            brand,
            {
                "headline": "Nadie más sobrevende",
                "cta": "Escríbeme por WhatsApp",
                "disclaimer": "",
            },
            size,
            kicker="OPERACIÓN TURÍSTICA",
        )
        insets = overlay.insets_for(size)
        for key, rect in info["rects"].items():
            assert rect, key
            assert rect[0] >= insets["left"] - 1, (size, key)
            assert rect[2] <= size[0] - insets["right"] + 1, (size, key)
            assert rect[1] >= insets["top"] - 1, (size, key)
            assert rect[3] <= size[1] - insets["bottom"] + 1, (size, key)
        assert not info["warnings"], (size, info["warnings"])


# ------------------------------------------------------------------- capture


def test_capture_keeps_proportion(tmp_path):
    brand = ads.load_brand(BRAND_PATH)
    shot = capture_file(tmp_path, "shot.png", (1600, 900))
    info = overlay.compose_capture(
        tmp_path / "out.png",
        brand,
        {"headline": "Tu día en una pantalla", "cta": "Escríbeme", "disclaimer": ""},
        (1080, 1350),
        shot,
        kicker="OPERACIÓN TURÍSTICA",
    )
    rect = info["rects"]["capture"]
    slot = info["slot"]
    drawn = (rect[2] - rect[0]) / (rect[3] - rect[1])
    # Same proportion as the file: a UI must never be squashed or cut.
    assert abs(drawn - 16 / 9) < 0.02
    assert rect[0] >= slot[0] and rect[2] <= slot[2]
    assert rect[1] >= slot[1] and rect[3] <= slot[3]


def test_capture_matches_concept_after_only(tmp_path, monkeypatch):
    stub(monkeypatch, tmp_path, count=2)
    first = capture_file(tmp_path, "one.png")
    second = capture_file(tmp_path, "two.png")
    path = campaign(tmp_path, "capture", captures=[first, second], concepts=2)
    args = ads.parse_args(["--campaign", str(path), "--only", "concept-2", "--ratios", "4:5"])
    assert ads.run(args) == 0
    manifest = json.loads((tmp_path / "out" / path.stem / "manifest.json").read_text())
    assets = [asset for asset in manifest["assets"] if asset["role"] == "final"]
    assert assets and {asset["concept"] for asset in assets} == {"concept-2"}
    assert {asset["capture"] for asset in assets} == {str(second)}


# --------------------------------------------------------------------- runs


def test_type_run_is_free(tmp_path, monkeypatch):
    stub(monkeypatch, tmp_path)
    path = campaign(tmp_path, "type")
    args = ads.parse_args(["--campaign", str(path), "--ratios", "1:1,9:16"])
    assert ads.run(args) == 0
    out = tmp_path / "out" / path.stem
    assert len(list((out / "finals").glob("*.png"))) == 4  # 2 ratios x A/B
    assert not list((out / "clean").glob("*"))  # no generated art at all
    assert (out / "contact-sheet.png").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["layout"] == "type"
    assert manifest["variants"] == ["A", "B"]
    assert manifest["totals"]["image_cost_usd"] == 0.0
    assert manifest["totals"]["cost_usd"] == 0.001


def test_variants_flag_limits_output(tmp_path, monkeypatch):
    stub(monkeypatch, tmp_path)
    path = campaign(tmp_path, "type")
    args = ads.parse_args(["--campaign", str(path), "--ratios", "1:1", "--variants", "A"])
    assert ads.run(args) == 0
    finals = list((tmp_path / "out" / path.stem / "finals").glob("*.png"))
    assert [finial.name for finial in finals] == ["concept-1-1x1-A.png"]
    ab_plan = (tmp_path / "out" / path.stem / "ab-plan.md").read_text()
    assert "concept-1-1x1-B.png" not in ab_plan


def test_on_image_over_limit_warns_and_wraps():
    plan = {
        "concepts": [
            dict(CONCEPTS[0], on_image=["Un texto bastante más largo", "B", "C"]),
        ],
        "ab_plan": AB_PLAN,
    }
    concepts, _, warnings = ads.normalize_plan(plan, 1)
    assert any("on_image" in warning for warning in warnings)
    # Wrapped, not cut: the layout shrinks the type instead of losing the word.
    assert concepts[0]["on_image"][0] == "Un texto bastante más largo"


def test_unknown_layout_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(campaign(tmp_path, "type").read_text().replace("layout: type", "layout: gif"))
    try:
        ads.load_campaign(path)
    except ads.ConfigError as exc:
        assert "layout" in str(exc)
    else:
        raise AssertionError("a bad layout must not load")


def test_campaign_on_image_is_authored_and_validated(tmp_path):
    path = campaign(tmp_path, "type")
    path.write_text(
        path.read_text()
        + 'on_image:\n  - "  Dos venden lo mismo  "\n  - "Tres reservas, una van"\n'
        + '  - "Nadie ve el horario"\n'
    )
    assert ads.load_campaign(path)["on_image"] == [
        "Dos venden lo mismo",
        "Tres reservas, una van",
        "Nadie ve el horario",
    ]

    path.write_text(path.read_text().replace('  - "Nadie ve el horario"\n', ""))
    with pytest.raises(ads.ConfigError, match="exactly 3"):
        ads.load_campaign(path)


CHAT = {
    "title": "Vans · sábado",
    "messages": [
        {"from": "Joel", "time": "08:12", "text": "Van 2 · 9:00 · salgo con 3"},
        {"from": "Karla", "time": "08:14", "text": "Van 2 · 9:00 · vendí 2 más"},
        {"from": "Recepción", "time": "08:19", "text": "Van 2 · 9:00 · 4 en recepción"},
    ],
}


def test_chat_layout_keeps_the_card_above_the_statement(tmp_path):
    brand = ads.load_brand(BRAND_PATH)
    copy = {"headline": "Tres reservas, una van", "subhead": "", "cta": "Mándame WhatsApp",
            "disclaimer": ""}
    for ratio in ((1080, 1080), (1080, 1350)):
        dest = tmp_path / f"chat-{ratio[1]}.png"
        info = overlay.compose_chat(dest, brand, copy, ratio, CHAT, kicker="OPERACIÓN TURÍSTICA")
        assert info["warnings"] == []
        assert not overlay.overlaps(info["rects"]["chat"], info["rects"]["text"])
        with Image.open(dest) as img:
            assert img.size == ratio


def test_chat_layout_survives_a_long_message(tmp_path):
    brand = ads.load_brand(BRAND_PATH)
    chat = {"title": "Vans", "messages": [dict(CHAT["messages"][0], text="x" * 90)]}
    copy = {"headline": "Tres reservas, una van", "subhead": "", "cta": "Escríbeme", "disclaimer": ""}
    info = overlay.compose_chat(tmp_path / "long.png", brand, copy, (1080, 1080), chat)
    assert not overlay.overlaps(info["rects"]["chat"], info["rects"]["text"])


def test_chat_campaign_needs_messages(tmp_path):
    path = campaign(tmp_path, "chat")
    with pytest.raises(ads.ConfigError, match="chat.messages"):
        ads.load_campaign(path)

    path.write_text(path.read_text() + 'chat:\n  messages:\n    - from: Joel\n      text: "a"\n')
    loaded = ads.load_campaign(path)
    assert loaded["chat"]["messages"][0]["from"] == "Joel"
