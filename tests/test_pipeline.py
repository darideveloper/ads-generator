"""Offline end-to-end check of the pipeline with stubbed API calls (no key, no cost)."""

import base64
import io
import json

from PIL import Image

import ads

CONCEPTS = [
    {
        "angle": "problem",
        "hook": "Café frío a media mañana",
        "headlines": ["Tu café en 25 segundos", "Sin colas, sin esperar", "Espresso listo ya"],
        "on_image": ["Espresso en 25 s", "Café en 25 segundos", "Nada de esperar"],
        "subhead": "La cafetera que cabe en cualquier oficina",
        "primary_text": "Prepara espresso en 25 segundos y olvídate de la máquina quemada.",
        "headline_short": "Café en 25 segundos",
        "description": "20% off primeras 500",
        "cta": "Comprar ahora",
        "image_prompt": "Compact espresso machine on a tidy office counter, morning light.",
    },
    {
        "angle": "proof",
        "hook": "1.200 oficinas ya lo cambiaron",
        "headlines": ["El café que sí sabe a café", "Limpieza en un botón", "30 días de prueba"],
        "on_image": ["Sabe a café", "Limpieza en un botón", "Prueba 30 días"],
        "subhead": "Miles de oficinas ya dejaron atrás la máquina vieja",
        "primary_text": "Una sola pieza lavable y espresso real. Cambia tu cafetera este mes.",
        "headline_short": "Café que sabe a café",
        "description": "Limpieza automática",
        "cta": "Ver más",
        "image_prompt": "Steam rising from a fresh espresso in a bright office kitchen.",
    },
    {
        "angle": "offer",
        "hook": "20% solo esta semana",
        "headlines": ["Ahorra 20% esta semana", "Últimas 500 unidades", "Precio de lanzamiento"],
        "on_image": ["Últimas 500", "Precio de lanzamiento", "Se acaba esta semana"],
        "subhead": "Oferta válida hasta agotar las primeras 500 unidades",
        "primary_text": "20% de descuento en la primera compra. Envío gratis a todo el país.",
        "headline_short": "Ahorra 20%",
        "description": "Solo 500 unidades",
        "cta": "Comprar ahora",
        "image_prompt": "Espresso machine beside a stack of coffee capsules, warm light.",
    },
]

AB_PLAN = {
    "primary_metric": "ctr",
    "guardrails": ["cpa", "cvr"],
    "tests": [
        {
            "title": "Headline A vs B",
            "concept_index": 3,
            "variants": ["A", "B"],
            "hypothesis": "Ahorro explícito gana a urgencia sola",
            "decision_rule": "Ganar con 95% de confianza y CPA dentro del 10%",
            "min_sample": "~4.000 impresiones por variante",
        }
    ],
}

CAPS = {
    "parameters": {
        "aspect_ratio": {"type": "enum", "values": ["1:1", "4:5", "9:16", "16:9"]},
        "resolution": {"type": "enum", "values": ["1K", "2K", "4K"]},
        "input_references": {"type": "range", "min": 0, "max": 14},
    },
    "references_max": 14,
}


def stub(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CONCURRENCY", "2")
    monkeypatch.setenv("IMAGE_RESOLUTION", "1K")
    monkeypatch.setattr(ads.openrouter, "image_capabilities", lambda model: CAPS)
    monkeypatch.setattr(ads.openrouter, "chat_json", lambda *a, **k: ({"concepts": CONCEPTS, "ab_plan": AB_PLAN}, 0.004))

    def fake_image(model, prompt, ratio, resolution, refs, caps):
        buffer = io.BytesIO()
        Image.new("RGBA", (1400, 1400), (20, 30, 45, 255)).save(buffer, "PNG")
        return {
            "b64": base64.b64encode(buffer.getvalue()).decode(),
            "media_type": "image/png",
            "cost": 0.03,
            "created": 0,
        }

    monkeypatch.setattr(ads.openrouter, "generate_image", fake_image)


def test_full_run(tmp_path, monkeypatch):
    stub(monkeypatch)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    args = ads.parse_args(
        ["--campaign", str(ads.ROOT / "campaigns/example.yaml"), "--ratios", "1:1,16:9"]
    )
    assert ads.run(args) == 0

    out = tmp_path / "out" / "example"
    assert (out / "plan.json").exists()
    assert (out / "copy.md").exists()
    assert (out / "ab-plan.md").exists()
    assert (out / "contact-sheet.png").exists()
    assert len(list((out / "clean").glob("concept-*"))) == 6
    # Default is AB: two variants, three concepts, two ratios.
    assert len(list((out / "finals").glob("*.png"))) == 12
    assert not list((out / "finals").glob("*-C.png"))
    assert len(list((out / "display").glob("*.jpg"))) == 6
    assert Image.open(out / "finals" / "concept-1-1x1-A.png").size == (1080, 1080)
    assert Image.open(out / "display" / "concept-1-1200x628-B.jpg").size == (1200, 628)

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["layout"] == "photo"
    assert manifest["totals"]["finals"] == 12
    assert manifest["totals"]["clean"] == 6
    assert manifest["totals"]["cost_usd"] == round(0.004 + 0.18, 4)

    # Re-running without --force must not spend again (cached art, existing finals).
    assert ads.run(args) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["totals"]["finals"] == 12
    assert manifest["totals"]["cost_usd"] == 0.004  # this run only


def test_budget_guard_skips(tmp_path, monkeypatch):
    stub(monkeypatch)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    args = ads.parse_args(
        [
            "--campaign",
            str(ads.ROOT / "campaigns/example.yaml"),
            "--ratios",
            "1:1",
            "--budget",
            "0.01",
        ]
    )
    assert ads.run(args) == 0
    manifest = json.loads((tmp_path / "out" / "example" / "manifest.json").read_text())
    assert manifest["totals"]["clean"] == 0
    assert any(a["status"] == "skipped_budget" for a in manifest["assets"])


def test_reuse_skips_the_text_model(tmp_path, monkeypatch):
    stub(monkeypatch)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    base = ["--campaign", str(ads.ROOT / "campaigns/example.yaml"), "--ratios", "1:1", "--layout", "type", "--variants", "A"]
    assert ads.run(ads.parse_args(base)) == 0

    def boom(*args, **kwargs):
        raise AssertionError("--reuse must not call the text model")

    monkeypatch.setattr(ads.openrouter, "chat_json", boom)
    assert ads.run(ads.parse_args(base + ["--reuse", "--force"])) == 0
    manifest = json.loads((tmp_path / "out" / "example" / "manifest.json").read_text())
    assert manifest["totals"]["text_cost_usd"] == 0.0
    assert manifest["totals"]["finals"] == 3
