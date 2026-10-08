"""OpenAI-compatible /chat/completions provider (JSON modules + vision judge)."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def image_to_data_url(path: str | Path) -> str:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model reply (fenced or bare)."""
    match = _JSON_FENCE.search(text)
    if match:
        text = match.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    return json.loads(text[start : end + 1])


class ChatCompletionsProvider:
    """JSON-module + vision client over any /chat/completions endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        vlm_model_id: str | None = None,
        timeout_s: float = 180.0,
        max_retries: int = 3,
    ):
        self.model_id = model_id
        self.vlm_model_id = vlm_model_id or model_id
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
        self._max_retries = max_retries

    async def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Network / API errors: exponential backoff, never change the request (PRD §19.1)."""
        import asyncio

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await self._client.post("/chat/completions", json=payload)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}: {response.text[:300]}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in (429, 500, 502, 503, 504):
                    raise
                logger.warning("chat attempt %d/%d failed: %s", attempt + 1, self._max_retries, exc)
                await asyncio.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"chat/completions failed after {self._max_retries} attempts: {last_error}")

    async def complete_json(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        purpose: str = "",
        image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        content: Any = user_payload
        if image_paths:
            content = [{"type": "text", "text": user_payload}] + [
                {"type": "image_url", "image_url": {"url": image_to_data_url(p)}} for p in image_paths
            ]
        payload: dict[str, Any] = {
            "model": self.vlm_model_id if image_paths else self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            data = await self._chat(payload)
        except httpx.HTTPStatusError as exc:
            # Endpoints without response_format support: retry once without it.
            if exc.response is not None and exc.response.status_code == 400:
                payload.pop("response_format", None)
                data = await self._chat(payload)
            else:
                raise
        reply = data["choices"][0]["message"].get("content") or ""
        return parse_json_object(reply)

    async def vision_json(
        self,
        prompt: str,
        image_paths: list[str],
        purpose: str = "",
    ) -> dict[str, Any]:
        return await self.complete_json(
            "Reply with a single JSON object only.", prompt, purpose=purpose, image_paths=image_paths
        )

    async def close(self) -> None:
        await self._client.aclose()
