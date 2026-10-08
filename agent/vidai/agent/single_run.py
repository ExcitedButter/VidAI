"""Orchestration entry points: wire the LLM / vision / Seedance clients, run one creative, persist it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from vidai.agent.harness import CreativePipeline, resume_group
from vidai.agent.providers import ChatCompletionsProvider, JsonClient, MockProvider, VisionClient
from vidai.agent.run_context import PauseCallback, PipelineContext, build_run_dir, find_plan_path
from vidai.agent.seedance import SeedanceClient, build_video_client
from vidai.config import VidaiSettings
from vidai.plan import CreativePlan, Status
from vidai.storage import DataLayout, write_record

logger = logging.getLogger(__name__)

__all__ = ["build_clients", "new_plan", "run_creative", "load_plan", "resume_group"]


def build_clients(settings: VidaiSettings) -> tuple[JsonClient, VisionClient, SeedanceClient]:
    if settings.mock:
        provider: JsonClient = MockProvider()
        vision: VisionClient = provider  # type: ignore[assignment]
    else:
        if not settings.llm_api_key:
            raise SystemExit("No planner LLM key configured (VIDAI_LLM_API_KEY / ARK_API_KEY). "
                             "Use --mock for an offline dry run.")
        if settings.llm_model.startswith("claude"):
            from vidai.agent.providers.anthropic_native import AnthropicProvider

            provider = AnthropicProvider(api_key=settings.llm_api_key, model_id=settings.llm_model,
                                         vlm_model_id=settings.vlm_model)
        else:
            provider = ChatCompletionsProvider(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                                               model_id=settings.llm_model, vlm_model_id=settings.vlm_model,
                                               reasoning_effort=settings.llm_reasoning_effort)
        vision = provider  # type: ignore[assignment]
    seedance = build_video_client(base_url=settings.seedance_base_url, api_key=settings.ark_api_key,
                                  model=settings.seedance_model, mock=settings.mock)
    return provider, vision, seedance


def new_plan(
    settings: VidaiSettings,
    product_url: str,
    *,
    mode: Optional[str] = None,
    duration: Optional[int] = None,
    product_text: str = "",
    product_image: Optional[str] = None,
    new_character: bool = False,
) -> CreativePlan:
    generation = settings.generation_settings()
    if duration:
        generation.targetDurationSec = duration
    plan = CreativePlan(mode=mode or settings.mode, productUrl=product_url,
                        productTextFallback=product_text or "", generationSettings=generation)
    if product_image:
        plan.userOverrides["productImage"] = str(Path(product_image).expanduser().resolve())
    if new_character:
        plan.userOverrides["newCharacter"] = True
    return plan


async def run_creative(
    plan: CreativePlan,
    settings: VidaiSettings,
    *,
    start_group: str = "product",
    stop_after: Optional[str] = None,
    on_pause: Optional[PauseCallback] = None,
) -> tuple[CreativePlan, Path, Optional[Path]]:
    """Run (or continue) the pipeline; returns (plan, run_dir, record_dir-if-finished)."""
    layout = DataLayout(settings.data_dir).ensure()
    run_dir = build_run_dir(layout.creatives_dir, plan)
    llm, vision, seedance = build_clients(settings)
    ctx = PipelineContext(plan=plan, settings=settings, llm=llm, vision=vision, seedance=seedance,
                          run_dir=run_dir, on_pause=on_pause)
    logger.info("creative %s v%d -> %s (from %s)", plan.creativeId, plan.version, run_dir, start_group)
    try:
        await CreativePipeline(ctx).run(start_group=start_group, stop_after=stop_after)
    finally:
        await llm.close()
        await seedance.close()
    record_dir: Optional[Path] = None
    if plan.status in (Status.READY, Status.FAILED):
        record_dir = write_record(layout, plan, run_dir)
    return plan, run_dir, record_dir


def load_plan(settings: VidaiSettings, ref: str, version: Optional[int] = None) -> tuple[CreativePlan, Path]:
    """`ref` is a creativeId (latest version unless given) or a path to a plan.json."""
    path = Path(ref).expanduser()
    if not path.is_file():
        path = find_plan_path(DataLayout(settings.data_dir).creatives_dir, ref, version)
    return CreativePlan.from_json(path.read_text(encoding="utf-8")), path
