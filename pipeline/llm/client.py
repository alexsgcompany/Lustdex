"""Minimal OpenRouter chat client (project rule 9: LLM batch with structured JSON).

One synchronous request per call, JSON object response, temperature 0 for
determinism. Model + key come from .env (rule 6). No streaming, no retries
beyond a single timeout — callers are one-off batch scripts that can resume.
"""

import json
import os

import httpx

import pipeline.common.config  # noqa: F401  (loads .env)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "google/gemini-3.1-flash-lite")


def chat_json(prompt: str, model: str = DEFAULT_MODEL, timeout: float = 60.0) -> dict:
    """Send a single prompt, return the parsed JSON object the model replies with."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")

    resp = httpx.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)
