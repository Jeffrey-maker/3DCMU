"""K2 Horizon reasoning client — isolated behind ReasoningClient so a wrong
schema guess or absent credentials costs one function, never a rewrite.

Schema is UNCONFIRMED against real IFM docs (best-guess: OpenAI-compatible
chat completions — see build plan decision 9). Run
backend/scripts/check_k2_connection.py once real credentials are set to
confirm or correct it with one live call before trusting this in production.

Both call sites in this app wrap every call in try/except with a
deterministic fallback — the core app must work with zero LLM calls, full
stop, independent of whether K2 Horizon is reachable or configured correctly.
"""

import json as _json
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.config import settings


class ReasoningClient(ABC):
    @abstractmethod
    def suggest_corridor_connectivity(self, draft_json: dict) -> Optional[dict]:
        """Given room/door/candidate-corridor-node coordinates as structured
        JSON, propose which rooms/doors connect to which corridor node. Draft
        suggestion only — a human confirms/edits in the graph editor. Returns
        None on any failure; never raises."""
        ...

    @abstractmethod
    def phrase_directions(
        self, deterministic_steps: list[str], context: dict
    ) -> Optional[str]:
        """Turn a deterministic step list into fluent natural language,
        optionally enriched with room-type/department context. Returns None
        on any failure; never raises — callers must fall back to the
        deterministic text."""
        ...


class NullReasoningClient(ReasoningClient):
    """Always-inert default. This is what makes 'the app works with zero LLM
    calls' true whenever credentials aren't configured."""

    def suggest_corridor_connectivity(self, draft_json: dict) -> Optional[dict]:
        return None

    def phrase_directions(
        self, deterministic_steps: list[str], context: dict
    ) -> Optional[str]:
        return None


class K2HorizonClient(ReasoningClient):
    """Best-guess OpenAI-compatible chat-completions schema — UNCONFIRMED.
    See module docstring."""

    def __init__(self, endpoint: str, api_key: str, model: str):
        self._endpoint = endpoint
        self._api_key = api_key
        self._model = model

    def _chat(self, prompt: str) -> Optional[str]:
        try:
            response = httpx.post(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    def suggest_corridor_connectivity(self, draft_json: dict) -> Optional[dict]:
        prompt = (
            "You are helping draft a corridor connectivity graph for an indoor "
            "floor plan. Given these room/door/candidate-corridor nodes as JSON "
            "(coordinates + labels), propose which rooms/doors most plausibly "
            "connect to which corridor node. Respond with ONLY a JSON object "
            'shaped {"edges": [{"from_node": str, "to_node": str}, ...]}, no '
            "prose.\n\n" + _json.dumps(draft_json)
        )
        content = self._chat(prompt)
        if content is None:
            return None
        try:
            return _json.loads(content)
        except ValueError:
            return None

    def phrase_directions(
        self, deterministic_steps: list[str], context: dict
    ) -> Optional[str]:
        prompt = (
            "Rephrase this turn-by-turn indoor walking direction list as fluent "
            "natural-language directions, optionally using the room-type/"
            "department context provided. Keep every step, don't invent new "
            "ones or drop any.\n\nSteps: "
            + _json.dumps(deterministic_steps)
            + "\n\nContext: "
            + _json.dumps(context)
        )
        return self._chat(prompt)


def get_reasoning_client() -> ReasoningClient:
    if (
        settings.enable_k2_horizon
        and settings.k2_horizon_endpoint
        and settings.k2_horizon_api_key
    ):
        return K2HorizonClient(
            endpoint=settings.k2_horizon_endpoint,
            api_key=settings.k2_horizon_api_key,
            model=settings.k2_horizon_model,
        )
    return NullReasoningClient()
