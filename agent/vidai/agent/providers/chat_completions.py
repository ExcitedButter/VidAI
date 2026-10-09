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
        reasoning_effort: str | None = None,
    ):
        self.model_id = model_id
        self.vlm_model_id = vlm_model_id or model_id
        # Reasoning models (gpt-6-astra, gpt-5*, o-series): `reasoning_effort` controls thinking
        # tokens, which are billed as output; "low" is plenty for schema-bound module calls.
        self.reasoning_effort = reasoning_effort or None
        self.last_usage: dict[str, Any] | None = None
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
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        data = await self._chat_with_param_fallback(payload)
        self.last_usage = data.get("usage")
        choice = data["choices"][0]
        reply = choice["message"].get("content") or ""
        if not reply.strip():
            raise ValueError(f"empty reply (finish_reason={choice.get('finish_reason')}) for {purpose or 'module'}")
        return parse_json_object(reply)

    async def _chat_with_param_fallback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """A 400 usually means the endpoint rejects an optional parameter; strip and retry once each."""
        for optional in ("response_format", "reasoning_effort", None):
            try:
                return await self._chat(payload)
            except httpx.HTTPStatusError as exc:
                if exc.response is None or exc.response.status_code != 400 or optional is None or optional not in payload:
                    raise
                logger.warning("400 from %s (%s); retrying without %s", payload.get("model"),
                               exc.response.text[:200], optional)
                payload.pop(optional, None)
        raise RuntimeError("unreachable")

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
