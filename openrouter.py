"""Minimal OpenRouter client: Chat Completions (JSON) + Image API.

Docs: https://openrouter.ai/docs/guides/overview/multimodal/image-generation
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path

import requests

DEFAULT_BASE = "https://openrouter.ai/api/v1"
TIMEOUT_CHAT = (10, 120)
TIMEOUT_IMAGE = (10, 300)
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3

MEDIA_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}


class OpenRouterError(RuntimeError):
    def __init__(self, status: int, message: str, body=None):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.message = message
        self.body = body


def _base() -> str:
    return os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE).rstrip("/")


def _headers() -> dict:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise OpenRouterError(0, "OPENROUTER_API_KEY is not set (copy .env.example to .env)")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Title": os.getenv("OPENROUTER_TITLE", "ads-generator"),
        "HTTP-Referer": os.getenv(
            "OPENROUTER_REFERER", "https://github.com/darideveloper/ads-generator"
        ),
    }


def _backoff(attempt: int, retry_after: str | None = None) -> None:
    if retry_after:
        try:
            time.sleep(min(float(retry_after), 30))
            return
        except ValueError:
            pass
    time.sleep((2, 5, 12)[min(attempt, 2)])


def _error(resp) -> tuple[str, object]:
    body = None
    try:
        body = resp.json()
    except ValueError:
        pass
    if isinstance(body, dict):
        message = (body.get("error") or {}).get("message")
        if message:
            return str(message), body
    return (resp.text[:400].strip() or resp.reason), body


def _request(method: str, path: str, payload=None, timeout=TIMEOUT_CHAT) -> dict:
    url = f"{_base()}{path}"
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.request(method, url, headers=_headers(), json=payload, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise OpenRouterError(0, f"{type(exc).__name__}: {exc}") from exc
            _backoff(attempt)
            continue
        if resp.status_code < 400:
            try:
                return resp.json()
            except ValueError as exc:
                raise OpenRouterError(resp.status_code, "response was not JSON") from exc
        # Retry only transient failures; 400/401/402/404/422 fail immediately.
        if resp.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
            _backoff(attempt, resp.headers.get("Retry-After"))
            continue
        message, body = _error(resp)
        raise OpenRouterError(resp.status_code, message, body)
    raise OpenRouterError(0, "request failed")


# --------------------------------------------------------------------------- chat


def _chat(model: str, messages: list, schema: dict | None, mode: str, require_params: bool = True):
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 4000,
    }
    if require_params:
        payload["provider"] = {"require_parameters": True}
    if schema is not None:
        if mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "ad_plan", "strict": True, "schema": schema},
            }
        else:
            payload["response_format"] = {"type": "json_object"}
    data = _request("POST", "/chat/completions", payload)
    choices = data.get("choices") or [{}]
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    cost = float((data.get("usage") or {}).get("cost") or 0.0)
    return (content or ""), cost


def _parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def chat_json(model: str, system: str, user: str, schema: dict) -> tuple[dict, float]:
    """Ask the text model for schema-shaped JSON. Returns (data, cost_usd)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        text, cost = _chat(model, messages, schema, "json_schema")
    except OpenRouterError as exc:
        if exc.status in (400, 404, 422):
            text, cost = _chat(model, messages, schema, "json_object", require_params=False)
        else:
            raise

    data = _parse_json(text)
    if data is not None:
        return data, cost

    repair = messages + [
        {"role": "assistant", "content": text[:6000]},
        {
            "role": "user",
            "content": (
                "Your previous reply was not valid JSON. Return ONLY the JSON object "
                "matching the schema. No prose, no markdown fences."
            ),
        },
    ]
    text2, cost2 = _chat(model, repair, schema, "json_object", require_params=False)
    data = _parse_json(text2)
    if data is None:
        raise OpenRouterError(0, "text model did not return valid JSON after one repair attempt")
    return data, cost + cost2


# -------------------------------------------------------------------------- image


@lru_cache(maxsize=8)
def image_capabilities(model: str) -> dict:
    """Per-endpoint capabilities for an image model (authoritative ratio/resolution list)."""
    data = _request("GET", f"/images/models/{model}/endpoints")
    endpoints = data.get("endpoints") or []
    if not endpoints:
        raise OpenRouterError(404, f"{model} has no image endpoints (not an image model?)")
    params: dict = {}
    refs_max = 0
    for endpoint in endpoints:
        for key, desc in (endpoint.get("supported_parameters") or {}).items():
            params.setdefault(key, desc)
            if key == "input_references" and isinstance(desc, dict):
                refs_max = max(refs_max, int(desc.get("max") or 0))
    return {
        "parameters": params,
        "references_max": refs_max,
        "providers": [e.get("provider_name") for e in endpoints],
    }


def generate_image(
    model: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    refs: list[str],
    caps: dict,
) -> dict:
    """One image. Only sends parameters the model actually declares."""
    params = caps.get("parameters") or {}
    payload: dict = {"model": model, "prompt": prompt, "provider": {"allow_fallbacks": True}}
    if "aspect_ratio" in params:
        payload["aspect_ratio"] = aspect_ratio
    if "resolution" in params:
        payload["resolution"] = resolution
    if refs and "input_references" in params:
        payload["input_references"] = [
            {"type": "image_url", "image_url": {"url": url}} for url in refs
        ]

    data = _request("POST", "/images", payload, timeout=TIMEOUT_IMAGE)
    items = data.get("data") or []
    if not items or not items[0].get("b64_json"):
        raise OpenRouterError(0, "image response contained no image data")
    item = items[0]
    return {
        "b64": item["b64_json"],
        "media_type": item.get("media_type") or "image/png",
        "cost": float((data.get("usage") or {}).get("cost") or 0.0),
        "created": data.get("created"),
    }


def write_image(b64: str, media_type: str, dest: Path) -> Path:
    """Decode base64 and write atomically, fixing the suffix to the real format."""
    dest = Path(dest).with_suffix(MEDIA_EXT.get(media_type, ".png"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(base64.b64decode(b64))
    os.replace(tmp, dest)
    return dest
