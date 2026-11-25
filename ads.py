#!/usr/bin/env python3
"""Ads generator.

brief + brand kit -> angles & copy (text model) -> art (image model, with product refs)
-> deterministic Pillow overlay -> platform exports + copy/AB docs + manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from PIL import Image

import openrouter
import overlay

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

RATIOS = {"1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920), "16:9": (1920, 1080)}
DEFAULT_RATIOS = ["1:1", "4:5"]
RATIO_SLUG = {"1:1": "1x1", "4:5": "4x5", "9:16": "9x16", "16:9": "16x9"}
DISPLAY = (1200, 628)
DISPLAY_ASPECT = DISPLAY[0] / DISPLAY[1]
VARIANTS = "ABC"
LAYOUTS = ("type", "capture", "photo")
ON_IMAGE_LIMIT = 24
COST_PRIOR = {"1K": 0.05, "2K": 0.15, "4K": 0.30}
REQUIRED_CAMPAIGN = ["product", "description", "benefits", "offer", "audience", "objective", "url"]

SYSTEM_PROMPT = (
    "You are a senior performance-marketing strategist and direct-response copywriter. "
    "You write clear, concrete, benefit-led ads without hype. Return only JSON."
)

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["concepts", "ab_plan"],
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "angle",
                    "hook",
                    "headlines",
                    "on_image",
                    "subhead",
                    "primary_text",
                    "headline_short",
                    "description",
                    "cta",
                    "image_prompt",
                ],
                "properties": {
                    "angle": {"type": "string", "enum": ["problem", "proof", "offer"]},
                    "hook": {"type": "string"},
                    "headlines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    # Text that goes INSIDE the creative, one per variant: 3-6 words, no period.
                    "on_image": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "subhead": {"type": "string"},
                    "primary_text": {"type": "string"},
                    "headline_short": {"type": "string"},
                    "description": {"type": "string"},
                    "cta": {"type": "string"},
                    "image_prompt": {"type": "string"},
                },
            },
        },
        "ab_plan": {
            "type": "object",
            "additionalProperties": False,
            "required": ["primary_metric", "guardrails", "tests"],
            "properties": {
                "primary_metric": {"type": "string"},
                "guardrails": {"type": "array", "items": {"type": "string"}},
                "tests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "title",
                            "concept_index",
                            "variants",
                            "hypothesis",
                            "decision_rule",
                            "min_sample",
                        ],
                        "properties": {
                            "title": {"type": "string"},
                            "concept_index": {"type": "integer"},
                            "variants": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["A", "B", "C"]},
                            },
                            "hypothesis": {"type": "string"},
                            "decision_rule": {"type": "string"},
                            "min_sample": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


class ConfigError(Exception):
    pass


# ------------------------------------------------------------------------ config


def resolve_path(value) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (ROOT / path)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_config() -> dict:
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise ConfigError("OPENROUTER_API_KEY is not set — copy .env.example to .env and add it")
    try:
        budget = float(os.getenv("MAX_COST_USD", "5"))
        concurrency = int(os.getenv("CONCURRENCY", "3"))
    except ValueError as exc:
        raise ConfigError(f"invalid numeric env value: {exc}") from exc
    return {
        "image_model": os.getenv("IMAGE_MODEL", "google/gemini-nano-banana-2.1").strip(),
        "text_model": os.getenv("TEXT_MODEL", "deepseek/deepseek-v4.1-flash").strip(),
        "resolution": os.getenv("IMAGE_RESOLUTION", "2K").strip(),
        "output_dir": resolve_path(os.getenv("OUTPUT_DIR", "out")),
        "budget": budget,
        "concurrency": max(1, concurrency),
    }


def load_brand(path: Path) -> dict:
    data = yaml.safe_load(path.read_text()) or {}
    if not data.get("name"):
        raise ConfigError(f"{path}: missing 'name'")
    fonts = data.get("fonts") or {}
    resolved = {
        "headline": resolve_path(fonts.get("headline") or overlay.DEFAULT_HEAD_FONT),
        "body": resolve_path(fonts.get("body") or overlay.DEFAULT_BODY_FONT),
    }
    resolved["block"] = resolve_path(fonts["block"]) if fonts.get("block") else resolved["headline"]
    resolved["mono"] = resolve_path(fonts["mono"]) if fonts.get("mono") else resolved["body"]
    for role, font in resolved.items():
        if not font.exists():
            raise ConfigError(f"{path}: {role} font not found: {font}")
    logo_path = resolve_path(data["logo"]) if data.get("logo") else None
    if logo_path and not logo_path.exists():
        raise ConfigError(f"{path}: logo not found: {logo_path}")
    colors = {
        "primary": "#0F172A",
        "text": "#FFFFFF",
        "muted": "#9FC3D8",
        "accent": "#22D3EE",
        "accent_text": "#A5F3FC",
        "on_accent": "#04121A",
        "line": "#1E2A32",
        "panel": "#0E1622",
        **(data.get("colors") or {}),
    }
    return {
        "name": data["name"],
        "logo_path": str(logo_path) if logo_path else None,
        "logo_height": data.get("logo_height"),
        "colors": colors,
        "fonts": {role: str(font) for role, font in resolved.items()},
        "tone": data.get("tone") or "",
        "disclaimer": data.get("disclaimer") or "",
        "default_cta": data.get("default_cta") or "",
    }


def load_campaign(path: Path) -> dict:
    data = yaml.safe_load(path.read_text()) or {}
    missing = [key for key in REQUIRED_CAMPAIGN if not data.get(key)]
    if missing:
        raise ConfigError(f"{path}: missing required fields: {', '.join(missing)}")
    if not isinstance(data["benefits"], list) or len(data["benefits"]) < 3:
        raise ConfigError(f"{path}: 'benefits' needs at least 3 items")
    if data["objective"] not in ("awareness", "conversion"):
        raise ConfigError(f"{path}: 'objective' must be awareness or conversion")
    language = data.get("language") or "es"
    if language not in ("es", "en"):
        raise ConfigError(f"{path}: 'language' must be es or en")
    layout = (data.get("layout") or "type").strip()
    if layout not in LAYOUTS:
        raise ConfigError(f"{path}: 'layout' must be one of {', '.join(LAYOUTS)}")
    try:
        concepts = int(data.get("concepts") or 1)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path}: 'concepts' must be an integer") from exc
    if not 1 <= concepts <= 6:
        raise ConfigError(f"{path}: 'concepts' must be between 1 and 6")
    refs = []
    for raw in data.get("refs") or []:
        ref = resolve_path(raw)
        if not ref.exists():
            raise ConfigError(f"{path}: ref not found: {ref}")
        refs.append(str(ref))
    captures = [resolve_path(raw) for raw in data.get("captures") or []]
    if data.get("capture"):
        captures = [resolve_path(data["capture"])]
    for capture in captures:
        if not capture.exists():
            raise ConfigError(f"{path}: capture not found: {capture}")
    if layout == "capture" and not captures:
        raise ConfigError(f"{path}: layout 'capture' needs 'capture' or 'captures'")
    if captures and len(captures) not in (1, concepts):
        raise ConfigError(
            f"{path}: 'captures' has {len(captures)} files, needs 1 or {concepts} (one per concept)"
        )
    statements = [" ".join(str(item).split()) for item in (data.get("on_image") or [])]
    if statements and len(statements) != 3:
        raise ConfigError(f"{path}: 'on_image' needs exactly 3 lines, one per variant (A, B, C)")
    for statement in statements:
        if len(statement) > ON_IMAGE_LIMIT:
            raise ConfigError(
                f"{path}: on_image '{statement}' is {len(statement)} chars (limit {ON_IMAGE_LIMIT})"
            )
    data["refs"] = refs
    data["on_image"] = statements
    data["captures"] = [str(capture) for capture in captures]
    data["layout"] = layout
    data["language"] = language
    data["concepts"] = concepts
    data["slug"] = path.stem
    return data


# -------------------------------------------------------------------------- plan


def _clip(text: str, limit: int, warnings: list[str], field: str, truncate: bool = True) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    warnings.append(f"{field} is {len(text)} chars (limit {limit})")
    if not truncate:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return cut or text[:limit]


COPY_RULES = """Copy rules (non-negotiable):
- {language_name} for ALL copy. No spanglish.
- Never write prices, percentages, discounts, "en X días" or any invented number.
  If there is no real data, do not invent it.
- No empty superlatives, no emojis, no exclamation marks.
- The villain is the process, never the client or their team.
- headlines: exactly 3, each <= 40 chars, each a different angle on the same idea.
- on_image: exactly 3, each <= 24 chars and 3 to 6 words, no trailing period.
  Count the characters: 25 is rejected. This is the big text printed inside the creative,
  so it must be one idea in short words, no comma chains.
- cta: starts with a verb, <= 18 chars."""

IMAGE_RULES = """The image_prompt must be in ENGLISH and describe a finished advertising photograph:
scene, lighting, materials, composition, camera feel. It must leave clean negative space in
the lower-left and upper-left thirds because the copy is overlaid later.
The image itself must contain NO text, letters, numbers, watermarks or logos.
The product must match the supplied reference photos exactly."""

NO_IMAGE_RULES = """image_prompt: one short ENGLISH sentence describing a possible photograph for this
concept. This layout draws the piece itself, so the sentence is context only, never rendered."""


def build_prompt(brand: dict, campaign: dict, count: int) -> str:
    layout = campaign["layout"]
    image_block = IMAGE_RULES if layout == "photo" else NO_IMAGE_RULES
    refs_note = (
        f"Reference photos supplied: {len(campaign.get('refs') or [])}."
        if layout == "photo"
        else "No photographs are rendered in this layout."
    )
    return f"""Brand: {brand['name']}
Brand tone: {brand['tone'] or 'confident, concrete, no hype'}
Objective: {campaign['objective']}
Layout: {layout}

Product: {campaign['product']}
Description: {campaign['description']}
Benefits: {json.dumps(campaign['benefits'], ensure_ascii=False)}
Offer: {campaign['offer']}
Audience: {campaign['audience']}
CTA: {campaign['cta']}
URL: {campaign['url']}
Pain points: {json.dumps(campaign.get('pain_points') or [], ensure_ascii=False)}
Differentiators: {json.dumps(campaign.get('differentiators') or [], ensure_ascii=False)}
Objections: {json.dumps(campaign.get('objections') or [], ensure_ascii=False)}

Produce exactly {count} concepts, in this angle order: problem, proof, offer.
Limits per concept: subhead <= 70, primary_text <= 125, headline_short <= 27,
description <= 27.

{COPY_RULES.format(language_name='Spanish (Mexico)' if campaign['language'] == 'es' else 'English')}

{image_block}
{refs_note}

ab_plan: primary_metric, guardrails, and 3 tests (one per wave) referencing
concept_index 1..{count} and variant pairs from ["A","B","C"].

Return only JSON matching the schema."""


def normalize_plan(plan: dict, count: int, needs_image: bool = True) -> tuple[list, dict, list[str]]:
    warnings: list[str] = []
    concepts = plan.get("concepts") or []
    if len(concepts) < count:
        raise ConfigError(f"text model returned {len(concepts)} concepts, {count} required")
    concepts = concepts[:count]
    for index, concept in enumerate(concepts, 1):
        concept["id"] = f"concept-{index}"
        concept["headlines"] = [
            _clip(headline, 40, warnings, f"{concept['id']} headline")
            for headline in (concept.get("headlines") or [])[:3]
        ]
        if len(concept["headlines"]) < 3:
            raise ConfigError(f"{concept['id']} needs exactly 3 headlines")
        # Wrapped, not truncated: a cut word is worse than a wrap on a 112 px statement.
        concept["on_image"] = [
            _clip(text, ON_IMAGE_LIMIT, warnings, f"{concept['id']} on_image", truncate=False)
            for text in (concept.get("on_image") or [])[:3]
        ]
        if len(concept["on_image"]) < 3:
            raise ConfigError(f"{concept['id']} needs exactly 3 on_image lines")
        concept["subhead"] = _clip(concept.get("subhead", ""), 70, warnings, f"{concept['id']} subhead")
        concept["primary_text"] = _clip(
            concept.get("primary_text", ""), 125, warnings, f"{concept['id']} primary_text", truncate=False
        )
        concept["headline_short"] = _clip(
            concept.get("headline_short", ""), 27, warnings, f"{concept['id']} headline_short"
        )
        concept["description"] = _clip(
            concept.get("description", ""), 27, warnings, f"{concept['id']} description"
        )
        concept["cta"] = _clip(concept.get("cta", ""), 18, warnings, f"{concept['id']} cta")
        concept["image_prompt"] = " ".join((concept.get("image_prompt") or "").split())
        if needs_image and not concept["image_prompt"]:
            raise ConfigError(f"{concept['id']} has an empty image_prompt")

    ab_plan = plan.get("ab_plan") or {}
    ab_plan.setdefault("primary_metric", "ctr")
    ab_plan.setdefault("guardrails", ["cpa", "cvr"])
    tests = ab_plan.get("tests") or []
    for test in tests:
        try:
            index = int(test.get("concept_index", 1))
        except (TypeError, ValueError):
            index = 1
        test["concept_index"] = min(max(index, 1), len(concepts))
        variants = [v for v in (test.get("variants") or []) if v in VARIANTS]
        test["variants"] = variants[:3] or ["A", "B"]
    ab_plan["tests"] = tests
    return concepts, ab_plan, warnings


def build_image_prompt(concept: dict, brand: dict, campaign: dict, ratio: str) -> str:
    # No hex codes here: image models ignore them and the noise pollutes the instruction.
    return (
        f"{concept['image_prompt']} Advertising photograph for {campaign['product']}. "
        "Palette: deep near-black charcoal surfaces with a single pale-ice cyan accent, "
        "cold neutral light. "
        "Premium commercial production, natural light, high detail, clean negative space "
        "in the lower-left and upper-left thirds reserved for overlaid copy. "
        "Absolutely no text, no letters, no words, no numbers, no watermarks and no logos "
        "anywhere in the image. Keep the referenced product identical to the photos: shape, "
        f"color, label and proportions unchanged. Audience: {campaign['audience']}. "
        f"Goal: {campaign['objective']}. Framing: {ratio}."
    )


def collect_refs(campaign: dict, caps: dict) -> tuple[list[str], list[str]]:
    """Product photos only. The logo is composited by Pillow, never sent to the model."""
    pool = [Path(path) for path in campaign.get("refs") or []]
    maximum = int(caps.get("references_max") or 0)
    if maximum <= 0:
        return [], [str(path) for path in pool]
    kept, dropped = pool[:maximum], pool[maximum:]
    urls = [overlay.ref_data_url(path, max_edge=1600)[0] for path in kept]
    return urls, [str(path) for path in dropped]


def validate_caps(caps: dict, ratios: list[str], resolution: str) -> None:
    params = caps.get("parameters") or {}
    ratio_param = params.get("aspect_ratio")
    if isinstance(ratio_param, dict):
        allowed = ratio_param.get("values") or []
        bad = [ratio for ratio in ratios if ratio not in allowed]
        if bad:
            raise ConfigError(
                f"aspect ratio(s) {', '.join(bad)} not supported by this image model. "
                f"Supported: {', '.join(map(str, allowed))}"
            )
    resolution_param = params.get("resolution")
    if isinstance(resolution_param, dict):
        allowed = resolution_param.get("values") or []
        if resolution not in allowed:
            raise ConfigError(
                f"resolution {resolution} not supported by this image model. "
                f"Supported: {', '.join(map(str, allowed))}"
            )


# ------------------------------------------------------------------------- output


def save_manifest(path: Path, manifest: dict, lock: threading.Lock) -> None:
    with lock:
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        os.replace(tmp, path)


def write_copy_md(path: Path, campaign: dict, brand: dict, concepts: list, warnings: list[str]) -> None:
    lines = [
        f"# Copy — {campaign['product']}",
        "",
        f"Brand: **{brand['name']}** · Objective: **{campaign['objective']}** · "
        f"Language: **{campaign['language']}** · Offer: **{campaign['offer']}**",
        "",
    ]
    for concept in concepts:
        lines += [f"## {concept['id']} — {concept['angle']}", ""]
        if concept.get("hook"):
            lines += [f"**Hook:** {concept['hook']}", ""]
        lines += [
            "| Variant | On image (max 24) | Space | Platform headline (max 40) | Space |",
            "| --- | --- | --- | --- | --- |",
        ]
        for variant, headline, on_image in zip(VARIANTS, concept["headlines"], concept["on_image"]):
            lines.append(
                f"| {variant} | {on_image} | {len(on_image)}/24 | {headline} | {len(headline)}/40 |"
            )
        lines += [
            "",
            f"**Primary text ({len(concept['primary_text'])}/125):** {concept['primary_text']}",
            "",
            f"**Headline short ({len(concept['headline_short'])}/27):** {concept['headline_short']}",
            "",
            f"**Description ({len(concept['description'])}/27):** {concept['description']}",
            "",
            f"**CTA:** {concept['cta']} → {campaign['url']}",
            "",
            f"**Image prompt:** {concept['image_prompt']}",
            "",
        ]
    if warnings:
        lines += ["## Warnings", ""] + [f"- {warning}" for warning in warnings] + [""]
    path.write_text("\n".join(lines))


def write_ab_md(
    path: Path, campaign: dict, ab_plan: dict, concepts: list, ratios: list[str], variants: str
) -> None:
    lines = [
        f"# A/B test plan — {campaign['product']}",
        "",
        f"Primary metric: **{ab_plan.get('primary_metric', 'ctr')}** · "
        f"Guardrails: {', '.join(ab_plan.get('guardrails') or []) or '—'}",
        "",
    ]
    for wave, test in enumerate(ab_plan.get("tests") or [], 1):
        concept = concepts[min(test["concept_index"], len(concepts)) - 1]
        served = [variant for variant in test["variants"] if variant in variants]
        if not served:
            continue
        files = [
            f"`finals/{concept['id']}-{RATIO_SLUG[ratio]}-{variant}.png`"
            for variant in served
            for ratio in ratios
        ]
        lines += [
            f"## Wave {wave} — {test.get('title', 'test')}",
            "",
            f"- Concept: **{concept['id']}** ({concept['angle']})",
            f"- Variants: **{', '.join(served)}**",
            f"- Hypothesis: {test.get('hypothesis', '—')}",
            f"- Decision rule: {test.get('decision_rule', '—')}",
            f"- Min sample: {test.get('min_sample', '—')}",
            f"- Files: {', '.join(files)}",
            "",
        ]
    lines += [
        "## Order of operations",
        "",
        "1. Run wave 1 on the offer concept: it usually moves the most.",
        "2. Keep the winner, retire the rest, then test the next concept's art.",
        "3. Only after copy and art settle, split by audience or placement.",
        "",
    ]
    path.write_text("\n".join(lines))


# --------------------------------------------------------------------------- run


def resolve_captures(campaign: dict, concepts: list) -> dict:
    """Map concept id -> capture path. Resolved before --only so filtering keeps the pairing."""
    files = campaign.get("captures") or []
    if not files:
        return {}
    if len(files) == 1:
        files = files * len(concepts)
    return {concept["id"]: files[index] for index, concept in enumerate(concepts)}


def run(args) -> int:
    config = load_config()
    if args.budget is not None:
        config["budget"] = args.budget
    if args.resolution:
        config["resolution"] = args.resolution

    campaign_path = resolve_path(args.campaign)
    if not campaign_path.exists():
        raise ConfigError(f"campaign not found: {campaign_path}")
    campaign = load_campaign(campaign_path)

    brand_name = args.brand or campaign.get("brand") or "daridev"
    brand_path = resolve_path(args.brand_path or f"brands/{brand_name}/brand.yaml")
    if not brand_path.exists():
        raise ConfigError(f"brand kit not found: {brand_path}")
    brand = load_brand(brand_path)

    campaign["cta"] = " ".join((campaign.get("cta") or brand.get("default_cta") or "").split())
    if not campaign["cta"]:
        raise ConfigError(f"{campaign_path}: no 'cta' and the brand has no 'default_cta'")

    layout = (args.layout or campaign["layout"]).strip()
    if layout not in LAYOUTS:
        raise ConfigError(f"layout must be one of: {', '.join(LAYOUTS)}")

    variants = (args.variants or "AB").upper()
    if any(variant not in VARIANTS for variant in variants) or len(set(variants)) != len(variants):
        raise ConfigError(f"--variants must be a subset of {VARIANTS}, e.g. AB")

    ratios = (
        [ratio.strip() for ratio in args.ratios.split(",") if ratio.strip()]
        if args.ratios
        else list(DEFAULT_RATIOS)
    )
    bad = [ratio for ratio in ratios if ratio not in RATIOS]
    if bad:
        raise ConfigError(f"unknown ratio(s): {', '.join(bad)}. Use: {', '.join(RATIOS)}")

    count = campaign["concepts"]
    refs, dropped_refs, caps = [], [], {}
    if layout == "photo":
        caps = openrouter.image_capabilities(config["image_model"])
        validate_caps(caps, ratios, config["resolution"])
        refs, dropped_refs = collect_refs(campaign, caps)

    kicker = campaign.get("kicker") or brand["name"]
    print(f"campaign : {campaign['slug']} — {campaign['product']}")
    print(
        f"models   : text={config['text_model']}"
        + (f" · image={config['image_model']} @ {config['resolution']}" if layout == "photo" else "")
    )
    print(f"layout   : {layout} · ratios: {', '.join(ratios)} · concepts: {count} · variants: {variants}")
    if layout == "photo":
        print(f"refs     : {len(refs)} attached" + (f", {len(dropped_refs)} dropped (model limit)" if dropped_refs else ""))

    plan, text_cost = openrouter.chat_json(
        config["text_model"], SYSTEM_PROMPT, build_prompt(brand, campaign, count), PLAN_SCHEMA
    )
    concepts, ab_plan, warnings = normalize_plan(plan, count, needs_image=layout == "photo")
    if campaign["on_image"]:
        # Authored copy wins over the model: three statements of the same pain, approved before
        # rendering. The model still writes the platform copy.
        for concept in concepts:
            concept["on_image"] = list(campaign["on_image"])
        warnings = [warning for warning in warnings if "on_image" not in warning]

    # Resolved before --only: the pairing is by concept id, not by position after filtering.
    captures = resolve_captures(campaign, concepts)
    if args.only:
        wanted = set(args.only)
        concepts = [concept for concept in concepts if concept["id"] in wanted]
        if not concepts:
            raise ConfigError(f"--only matched no concepts: {', '.join(sorted(wanted))}")
    if layout == "capture" and not captures:
        raise ConfigError("layout 'capture' needs 'capture' or 'captures' in the campaign")

    out_dir = config["output_dir"] / campaign["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / "plan.json"
    plan_path.write_text(
        json.dumps({"concepts": concepts, "ab_plan": ab_plan}, indent=2, ensure_ascii=False)
    )

    if args.dry_run:
        for concept in concepts:
            print(f"\n{concept['id']} [{concept['angle']}]")
            print(f"  hook      : {concept.get('hook', '')}")
            for variant, headline, on_image in zip(VARIANTS, concept["headlines"], concept["on_image"]):
                print(f"  {variant} image  : {on_image} ({len(on_image)}/24)")
                print(f"  {variant} social : {headline} ({len(headline)}/40)")
            print(f"  subhead   : {concept['subhead']}")
            print(f"  primary   : {concept['primary_text']}")
            print(f"  cta       : {concept['cta']}")
            print(f"  prompt    : {concept['image_prompt']}")
            print(
                "  files     : "
                + ", ".join(
                    f"finals/{concept['id']}-{RATIO_SLUG[ratio]}-{variant}.png"
                    for ratio in ratios
                    for variant in variants
                )
            )
        print(f"\ndry run: nothing rendered · plan saved to {display_path(plan_path)}")
        print(f"text cost: ${text_cost:.4f}")
        for warning in warnings:
            print(f"warning  : {warning}")
        return 0

    clean_dir = out_dir / "clean"
    finals_dir = out_dir / "finals"
    display_dir = out_dir / "display"
    for directory in (clean_dir, finals_dir, display_dir):
        directory.mkdir(parents=True, exist_ok=True)

    manifest = {
        "campaign": campaign["slug"],
        "brand": brand["name"],
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layout": layout,
        "variants": list(variants),
        "models": (
            {"image": config["image_model"], "text": config["text_model"]}
            if layout == "photo"
            else {"text": config["text_model"]}
        ),
        "resolution": config["resolution"] if layout == "photo" else None,
        "budget_usd": config["budget"],
        "assets": [],
        "warnings": warnings + [f"dropped ref (model limit): {path}" for path in dropped_refs],
        "totals": {},
    }
    manifest_path = out_dir / "manifest.json"
    manifest_lock = threading.Lock()
    save_manifest(manifest_path, manifest, manifest_lock)

    state = {"spent": text_cost, "reserved": 0.0}
    guard = threading.Lock()

    def clean_existing(concept_id: str, ratio: str):
        for suffix in (".png", ".jpg", ".webp"):
            candidate = clean_dir / f"{concept_id}-{RATIO_SLUG[ratio]}{suffix}"
            if candidate.exists():
                return candidate
        return None

    def render(concept: dict, ratio: str) -> dict:
        entry = {
            "concept": concept["id"],
            "ratio": ratio,
            "role": "clean",
            "status": "ok",
            "model": config["image_model"],
            "cost_usd": 0.0,
        }
        existing = clean_existing(concept["id"], ratio)
        if existing and not args.force:
            entry.update(status="cached", file=str(existing.relative_to(out_dir)))
            return entry

        prior = COST_PRIOR.get(config["resolution"], 0.15)
        with guard:
            if state["spent"] + state["reserved"] + prior > config["budget"]:
                entry.update(status="skipped_budget", budget_usd=config["budget"])
                return entry
            state["reserved"] += prior

        prompt = build_image_prompt(concept, brand, campaign, ratio)
        try:
            result = openrouter.generate_image(
                config["image_model"], prompt, ratio, config["resolution"], refs, caps
            )
        except openrouter.OpenRouterError as exc:
            entry.update(status="error", error=str(exc))
            return entry
        else:
            with guard:
                state["spent"] += result["cost"]
            try:
                written = openrouter.write_image(
                    result["b64"],
                    result["media_type"],
                    clean_dir / f"{concept['id']}-{RATIO_SLUG[ratio]}.png",
                )
            except (OSError, ValueError) as exc:
                entry.update(
                    status="error",
                    error=f"could not write image: {exc}",
                    cost_usd=result["cost"],
                )
                return entry
            entry.update(
                file=str(written.relative_to(out_dir)),
                cost_usd=result["cost"],
                media_type=result["media_type"],
                prompt=prompt,
                refs=len(refs),
            )
            return entry
        finally:
            with guard:
                state["reserved"] -= prior

    clean_map = {}
    if layout == "photo":
        tasks = [(concept, ratio) for concept in concepts for ratio in ratios]
        with ThreadPoolExecutor(max_workers=config["concurrency"]) as pool:
            futures = [pool.submit(render, concept, ratio) for concept, ratio in tasks]
            for future in as_completed(futures):
                entry = future.result()
                manifest["assets"].append(entry)
                save_manifest(manifest_path, manifest, manifest_lock)
        clean_map = {
            (entry["concept"], entry["ratio"]): entry["file"]
            for entry in manifest["assets"]
            if entry["role"] == "clean" and entry["status"] in ("ok", "cached")
        }

    def copy_for(concept: dict, variant: str) -> dict:
        return {
            # The text printed inside the piece is on_image; the 40-char headline is for the platform.
            "headline": concept["on_image"][VARIANTS.index(variant)],
            "subhead": concept["subhead"],
            "cta": concept["cta"] or campaign["cta"],
            "disclaimer": campaign.get("disclaimer") or brand.get("disclaimer") or "",
        }

    def compose_final(concept: dict, ratio: str, variant: str, dest) -> dict:
        copy = copy_for(concept, variant)
        if layout == "photo":
            return overlay.compose(
                out_dir / clean_map[(concept["id"], ratio)], dest, brand, copy,
                target_size=RATIOS[ratio],
            )
        if layout == "capture":
            return overlay.compose_capture(
                dest, brand, copy, RATIOS[ratio], captures[concept["id"]], kicker=kicker
            )
        return overlay.compose_type(dest, brand, copy, RATIOS[ratio], kicker=kicker)

    def compose_display(concept: dict, variant: str, dest) -> dict:
        copy = copy_for(concept, variant)
        if layout == "photo":
            return overlay.compose(
                out_dir / clean_map[(concept["id"], "16:9")], dest, brand, copy,
                target_size=DISPLAY, crop_aspect=DISPLAY_ASPECT, jpeg=True,
            )
        if layout == "capture":
            return overlay.compose_capture(
                dest, brand, copy, DISPLAY, captures[concept["id"]], kicker=kicker, jpeg=True
            )
        return overlay.compose_type(dest, brand, copy, DISPLAY, kicker=kicker, jpeg=True)

    def entry_for(concept, ratio, variant, role, dest, info=None, size=None):
        entry = {
            "concept": concept["id"],
            "ratio": ratio,
            "variant": variant,
            "role": role,
            "status": "ok" if info else "cached",
            "file": str(dest.relative_to(out_dir)),
            "size": list(info["output_size"]) if info else size,
            "warnings": info["warnings"] if info else [],
        }
        if layout == "capture":
            entry["capture"] = display_path(Path(captures[concept["id"]]))
        return entry

    for concept in concepts:
        for ratio in ratios:
            if layout == "photo" and (concept["id"], ratio) not in clean_map:
                continue
            for variant in variants:
                dest = finals_dir / f"{concept['id']}-{RATIO_SLUG[ratio]}-{variant}.png"
                if dest.exists() and not args.force:
                    with Image.open(dest) as cached:
                        size = list(cached.size)
                    manifest["assets"].append(entry_for(concept, ratio, variant, "final", dest, size=size))
                    continue
                info = compose_final(concept, ratio, variant, dest)
                manifest["assets"].append(entry_for(concept, ratio, variant, "final", dest, info=info))
            if ratio == "16:9":
                for variant in variants:
                    dest = display_dir / f"{concept['id']}-1200x628-{variant}.jpg"
                    if dest.exists() and not args.force:
                        with Image.open(dest) as cached:
                            size = list(cached.size)
                        manifest["assets"].append(
                            entry_for(concept, "1.91:1", variant, "display", dest, size=size)
                        )
                        continue
                    info = compose_display(concept, variant, dest)
                    manifest["assets"].append(
                        entry_for(concept, "1.91:1", variant, "display", dest, info=info)
                    )

    finals = [a for a in manifest["assets"] if a["role"] == "final"]
    overlay.contact_sheet(
        [(out_dir / asset["file"], f"{asset['concept']} · {asset['ratio']} · {asset['variant']}")
         for asset in finals],
        out_dir / "contact-sheet.png",
        brand,
    )
    write_copy_md(out_dir / "copy.md", campaign, brand, concepts, warnings)
    write_ab_md(out_dir / "ab-plan.md", campaign, ab_plan, concepts, ratios, variants)

    display = [a for a in manifest["assets"] if a["role"] == "display"]
    errors = [a for a in manifest["assets"] if a["status"] in ("error", "skipped_budget")]
    image_cost = sum(float(a.get("cost_usd") or 0.0) for a in manifest["assets"])
    manifest["totals"] = {
        "layout": layout,
        "clean": len(clean_map),
        "finals": len(finals),
        "display": len(display),
        "errors": len(errors),
        "image_cost_usd": round(image_cost, 4),
        "text_cost_usd": round(text_cost, 4),
        "cost_usd": round(image_cost + text_cost, 4),
        "budget_usd": config["budget"],
    }
    save_manifest(manifest_path, manifest, manifest_lock)

    print(
        f"\nrendered : {layout} · {len(clean_map)} art · {len(finals)} finals · "
        f"{len(display)} display"
    )
    print(f"cost     : ${image_cost + text_cost:.4f} (budget ${config['budget']:.2f})")
    print(f"output   : {display_path(out_dir)}")
    if errors:
        print(f"problems : {len(errors)} — see manifest.json")
        for entry in errors[:5]:
            print(f"  - {entry['concept']} {entry['ratio']}: {entry.get('error', entry['status'])}")
    warned = [asset for asset in finals if asset.get("warnings")]
    if warned:
        print(f"warnings : {len(warned)} finals with layout warnings — see manifest.json")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Generate a full ad set from a campaign brief.")
    parser.add_argument("--campaign", required=True, help="path to campaigns/<name>.yaml")
    parser.add_argument("--brand", help="brand name under brands/ (default: campaign.brand)")
    parser.add_argument("--brand-path", help="explicit path to brand.yaml")
    parser.add_argument("--dry-run", action="store_true", help="plan + prompts only, no images")
    parser.add_argument("--only", action="append", help="render only these concepts (repeatable)")
    parser.add_argument("--layout", help=f"override campaign layout: {', '.join(LAYOUTS)}")
    parser.add_argument("--variants", help="variants to render, default AB (subset of ABC)")
    parser.add_argument("--ratios", help=f"comma list, default {', '.join(DEFAULT_RATIOS)}")
    parser.add_argument("--resolution", help="override IMAGE_RESOLUTION (1K/2K/4K)")
    parser.add_argument("--budget", type=float, help="override MAX_COST_USD for this run")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except openrouter.OpenRouterError as exc:
        print(f"openrouter: {exc}", file=sys.stderr)
        return 2 if exc.status in (401, 402) else 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
