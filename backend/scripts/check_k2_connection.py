"""Standalone diagnostic — NOT part of the app's request path.

Run this once locally after setting K2_HORIZON_ENDPOINT / K2_HORIZON_API_KEY
/ K2_HORIZON_MODEL (as env vars, or in a gitignored backend/.env) to fire one
real request at K2 Horizon and compare the RAW response against what our
best-guess (OpenAI-compatible chat-completions) parser in app/llm/client.py
extracts from it.

This is the concrete way to confirm or correct the schema guess, per the
build spec's own instruction to confirm the real request/response schema
with a live call before building around it. If the shape differs, only
K2HorizonClient._chat()'s response parsing needs to change.

Usage (from backend/, with your venv active):
    python scripts/check_k2_connection.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402


def main() -> None:
    if not settings.k2_horizon_endpoint or not settings.k2_horizon_api_key:
        print(
            "K2_HORIZON_ENDPOINT and/or K2_HORIZON_API_KEY are not set.\n"
            "Set them, then re-run, e.g.:\n"
            "  export K2_HORIZON_ENDPOINT=https://...\n"
            "  export K2_HORIZON_API_KEY=...\n"
            "  export K2_HORIZON_MODEL=...   # optional, defaults to 'k2-horizon'\n"
            "  python scripts/check_k2_connection.py"
        )
        raise SystemExit(1)

    print(f"Endpoint: {settings.k2_horizon_endpoint}")
    print(f"Model:    {settings.k2_horizon_model}")
    print("Sending a minimal OpenAI-compatible-shaped chat request...\n")

    request_body = {
        "model": settings.k2_horizon_model,
        "messages": [
            {"role": "user", "content": "Reply with exactly the word: pong"}
        ],
        "temperature": 0.0,
    }

    try:
        response = httpx.post(
            settings.k2_horizon_endpoint,
            headers={
                "Authorization": f"Bearer {settings.k2_horizon_api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        print(f"Request failed before getting a response: {exc!r}")
        print("Check the endpoint URL and network access first.")
        raise SystemExit(1)

    print(f"HTTP status: {response.status_code}\n")
    print("--- Raw response body ---")
    raw = None
    try:
        raw = response.json()
        print(json.dumps(raw, indent=2))
    except ValueError:
        print(response.text)

    print("\n--- What our OpenAI-compatible-shape parser extracts ---")
    if raw is None:
        print("(response wasn't JSON — see raw text above; adjust the parser accordingly)")
        return
    try:
        content = raw["choices"][0]["message"]["content"]
        print(repr(content))
        print(
            "\nSchema guess matches — app/llm/client.py's K2HorizonClient "
            "should work as-is."
        )
    except (KeyError, IndexError, TypeError) as exc:
        print(f"Parser failed: {exc!r}")
        print(
            "\nThe real response doesn't match the OpenAI-compatible shape "
            "assumed in app/llm/client.py. Update K2HorizonClient._chat()'s "
            "response parsing in that file to match the raw shape printed "
            "above — that's the only place that needs to change."
        )


if __name__ == "__main__":
    main()
