"""Stage base + the structured-prompt module call (PRD §17.1, retry rules §19.1)."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from vidai.agent.prompts import load_prompt
from vidai.agent.run_context import PipelineContext

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


class StageError(RuntimeError):
    """A stage could not produce its output; the pipeline turns this into FAILED."""


class Stage(ABC):
    name: str = "stage"

    @abstractmethod
    async def run(self, ctx: PipelineContext) -> None: ...


def _compact(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


async def call_module(
    ctx: PipelineContext,
    prompt_name: str,
    payload: dict[str, Any],
    *,
    model: Optional[type[TModel]] = None,
    image_paths: Optional[list[str]] = None,
    purpose: Optional[str] = None,
) -> Any:
    """Run one module prompt: structured JSON in, schema-validated JSON out.

    * LLM returns invalid JSON / schema -> same-input schema-repair retry (PRD §19.1).
    * network / API errors -> the provider backs off and retries without changing the request;
      one more outer retry here, then the stage fails.
    """
    system_prompt = load_prompt(prompt_name)
    user_payload = _compact(payload)
    purpose = purpose or prompt_name
    repairs = ctx.settings.schema_repair_retries
    last_error: Exception | None = None
    attempt_payload = user_payload
    for attempt in range(repairs + 1):
        try:
            reply = await ctx.llm.complete_json(
                system_prompt, attempt_payload, purpose=purpose, image_paths=image_paths
            )
        except Exception as exc:  # network / provider failure
            last_error = exc
            ctx.trace("module_error", module=prompt_name, attempt=attempt, error=str(exc)[:500])
            logger.warning("module %s provider error (attempt %d): %s", prompt_name, attempt, exc)
            await asyncio.sleep(min(2 ** attempt, 10))
            continue
        ctx.trace("module_reply", module=prompt_name, attempt=attempt, reply=_truncate(reply))
        if model is None:
            return reply
        try:
            return model.model_validate(reply)
        except ValidationError as exc:
            last_error = exc
            logger.warning("module %s schema invalid (attempt %d): %s", prompt_name, attempt, exc)
            attempt_payload = (
                f"{user_payload}\n\n<schema_repair>Your previous reply failed schema validation:\n"
                f"{str(exc)[:1500]}\nReturn the corrected JSON object only.</schema_repair>"
            )
    raise StageError(f"module {prompt_name} failed after {repairs + 1} attempts: {last_error}")


def _truncate(value: Any, limit: int = 4000) -> Any:
    text = _compact(value)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def speech_seconds(text: str, words_per_minute: int) -> float:
    """Spoken-duration estimate: words / wpm plus a short natural pause per beat (PRD §9.2)."""
    words = len([w for w in text.replace("\n", " ").split(" ") if w.strip()])
    return round(words / max(words_per_minute, 60) * 60.0 + 0.4, 2)
