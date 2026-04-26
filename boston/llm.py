from __future__ import annotations

import logging
from typing import Any

import httpx


LOGGER = logging.getLogger(__name__)


class LocalLLMClient:
    def __init__(self, endpoint: str, model_name: str, timeout_seconds: int = 15) -> None:
        self.endpoint = endpoint
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=3) as client:
                response = client.post(
                    self.endpoint,
                    json={
                        "model": self.model_name,
                        "prompt": "Reply with exactly: ok",
                        "stream": False,
                    },
                )
                return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        full_prompt = prompt if not system_prompt else f"System: {system_prompt}\nUser: {prompt}"
        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": full_prompt,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(self.endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                answer = data.get("response") or data.get("text") or ""
                return str(answer).strip() or "I am online and ready."
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Local LLM call failed")
            return (
                "I could not reach the local model right now. "
                f"Please check model service status. Error: {exc}"
            )
