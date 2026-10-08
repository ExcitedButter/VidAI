"""Provider protocols: structured-JSON LLM calls + a vision judge (PRD §17.1).

Every module prompt takes structured input and must return a fixed JSON schema, so the only
planner capability the pipeline needs is `complete_json`. `vision_json` serves Video QC.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class JsonClient(Protocol):
    """Runs one structured-prompt module and returns its JSON object."""

    model_id: str

    async def complete_json(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        purpose: str = "",
        image_paths: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


@runtime_checkable
class VisionClient(Protocol):
    """Answers structured-JSON questions about a set of images."""

    async def vision_json(
        self,
        prompt: str,
        image_paths: list[str],
        purpose: str = "",
    ) -> dict[str, Any]: ...
