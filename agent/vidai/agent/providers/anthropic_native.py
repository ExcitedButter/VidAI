"""Native Anthropic (Claude) provider for the vidai agent loop.

The harness speaks the OpenAI chat-completions message shape; this provider
translates to/from the Anthropic Messages API:

- OpenAI `tools` function schemas    -> Anthropic `tools` (name/description/input_schema)
- assistant `tool_calls`             -> `tool_use` content blocks
- `role: "tool"` results             -> `tool_result` blocks, merged into ONE user turn
- `image_url` data-URL parts         -> base64 `image` blocks
- Claude `tool_use` blocks           -> OpenAI-shaped `tool_calls` for the harness

Raw Anthropic content blocks (including thinking blocks) are cached on each
assistant message under `_anthropic_content` and replayed verbatim on later
turns, as required when continuing a conversation on the same model.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from vidai.agent.providers.chat_completions import image_to_data_url, parse_json_object

logger = logging.getLogger(__name__)

_MAX_TOKENS = 16000
# Refusal fallbacks on by default for Opus 5-family models (server routes by category).
_BETA_HEADERS = {"anthropic-beta": "server-side-fallback-2026-07-01"}
_FALLBACKS_BODY = {"fallbacks": "default"}


def _data_url_to_image_block(url: str) -> dict[str, Any]:
    header, _, data = url.partition(",")
    media_type = header.removeprefix("data:").split(";")[0] or "image/jpeg"
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


class AnthropicProvider:
    """Planner + vision client over the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        vlm_model_id: str | None = None,
        timeout_s: float = 600.0,
        max_retries: int = 3,
    ):
        from anthropic import AsyncAnthropic

        self.model_id = model_id
        self.vlm_model_id = vlm_model_id or model_id
        self._client = AsyncAnthropic(
            api_key=api_key or None, timeout=timeout_s, max_retries=max_retries
        )

    # ------------------------------------------------------------ conversion
    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for tool in tools:
            fn = tool.get("function", tool)
            converted.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object"}),
                }
            )
        return converted

    @staticmethod
    def _convert_user_content(content: Any) -> Any:
        if isinstance(content, str):
            return content
        blocks: list[dict[str, Any]] = []
        for part in content:
            if part.get("type") == "text":
                blocks.append({"type": "text", "text": part.get("text", "")})
            elif part.get("type") == "image_url":
                blocks.append(_data_url_to_image_block(part["image_url"]["url"]))
        return blocks or " "

    def _convert_history(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []

        def flush_results() -> None:
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": str(msg.get("content", "")),
                    }
                )
                continue
            flush_results()
            if role == "user":
                out.append({"role": "user", "content": self._convert_user_content(msg.get("content"))})
            elif role == "assistant":
                raw = msg.get("_anthropic_content")
                if raw is not None:
                    out.append({"role": "assistant", "content": raw})
                    continue
                blocks: list[dict[str, Any]] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": str(msg["content"])})
                for call in msg.get("tool_calls") or []:
                    fn = call.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    blocks.append(
                        {"type": "tool_use", "id": call.get("id", ""), "name": fn.get("name", ""), "input": args}
                    )
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
        flush_results()
        return out

    @staticmethod
    def _to_openai_message(response: Any) -> dict[str, Any]:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {"name": block.name, "arguments": json.dumps(block.input)},
                    }
                )
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(text_parts),
            "_anthropic_content": [block.model_dump(exclude_none=True) for block in response.content],
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    # ------------------------------------------------------------------ API
    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": _MAX_TOKENS,
            "system": system_prompt,
            "messages": self._convert_history(messages),
            "extra_headers": _BETA_HEADERS,
            "extra_body": _FALLBACKS_BODY,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
        response = await self._client.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(f"model refused the request: {details}")
        return self._to_openai_message(response)

    async def complete_json(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        purpose: str = "",
        image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """One structured-prompt module call (PRD §17.1): JSON in, JSON out."""
        content: list[dict[str, Any]] = [{"type": "text", "text": user_payload}]
        for path in image_paths or []:
            content.append(_data_url_to_image_block(image_to_data_url(path)))
        response = await self._client.messages.create(
            model=self.vlm_model_id if image_paths else self.model_id,
            max_tokens=_MAX_TOKENS,
            system=system_prompt + "\n\nReply with a single JSON object and nothing else.",
            messages=[{"role": "user", "content": content}],
            extra_headers=_BETA_HEADERS,
            extra_body=_FALLBACKS_BODY,
        )
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(f"model refused the request: {details}")
        reply = "\n".join(b.text for b in response.content if b.type == "text")
        return parse_json_object(reply)

    async def vision_json(
        self,
        prompt: str,
        image_paths: list[str],
        purpose: str = "",
    ) -> dict[str, Any]:
        return await self.complete_json(
            "You are a strict video QA judge.", prompt, purpose=purpose, image_paths=image_paths
        )

    async def close(self) -> None:
        await self._client.close()
