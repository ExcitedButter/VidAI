"""Run the MasSurge pipeline (auto mode) over several product URLs and write a results folder.

    python scripts/run_batch.py --out results https://shop.example/products/a https://... 
    python scripts/run_batch.py --urls-file urls.txt --duration 15 [--mock]

Output: <out>/<timestamp>/summary.md (+ summary.json), one <slug>.mp4 and <slug>.plan.json per
creative. summary.md is rewritten after every creative, so partial progress is always visible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidai.agent.media import ffprobe_metadata  # noqa: E402
from vidai.agent.run_context import slugify_hint  # noqa: E402
from vidai.agent.single_run import new_plan, run_creative  # noqa: E402
from vidai.config import VidaiSettings  # noqa: E402
from vidai.plan import CreativePlan, Status  # noqa: E402
from vidai.storage.records import compute_metrics  # noqa: E402


def _row(plan: CreativePlan, url: str, minutes: float, final_s: float | None, error: str | None = None) -> dict[str, Any]:
    angle, character, audience = plan.sellingAngle, plan.character, plan.audience
    sqc, vqc = plan.script_qc, plan.video_qc
    return {
        "creativeId": plan.creativeId, "url": url, "status": plan.status.value,
        "product": plan.product.productName if plan.product else None,
        "brand": plan.product.brandName if plan.product else None,
        "productConfidence": plan.product.confidence if plan.product else None,
        "audience": audience.segmentName if audience else None,
        "audienceEvidence": audience.evidence if audience else None,
        "angleFamily": angle.angleFamily if angle else None,
        "angleIdea": angle.oneLineIdea if angle else None,
        "archetype": plan.scriptStrategy.archetype if plan.scriptStrategy else None,
        "character": character.name if character else None,
        "characterSource": character.source if character else None,
        "scriptQC": sqc.overall if sqc else None, "scriptRepairRounds": sqc.repairRounds if sqc else None,
        "scriptEstSec": sqc.estimatedDurationSec if sqc else None,
        "beats": len(plan.speechVisualBeats), "shots": len(plan.shotPlan),
        "shotAttempts": sum(s.attempts for s in plan.shotPlan),
        "videoQC": vqc.overall if vqc else None, "videoRepairRounds": vqc.repairRounds if vqc else None,
        "finalSec": final_s, "minutes": round(minutes, 1),
        "metrics": compute_metrics(plan, final_s),
        "warnings": list(plan.warnings), "error": error or plan.error,
        "script": [{"beatId": b.beatId, "type": b.visual.type, "speech": b.speech} for b in plan.speechVisualBeats],
        "why": plan.why_this_creative(),
    }


def _write_summary(out: Path, rows: list[dict[str, Any]], settings: VidaiSettings) -> None:
    (out / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# MasSurge batch — {out.name}", "",
             f"LLM `{'mock' if settings.mock else settings.llm_model}`"
             + (f" (reasoning_effort={settings.llm_reasoning_effort})" if settings.llm_reasoning_effort and not settings.mock else "")
             + f" · video `{'mock' if settings.mock else settings.seedance_model}` · target {settings.target_duration_s}s {settings.aspect_ratio} {settings.resolution}",
             "", "| product | status | audience | angle | archetype | character | script QC | video QC | final | retry rate | min |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        sqc = f"{r['scriptQC']} ({r['scriptRepairRounds']} rep, {r['scriptEstSec'] or 0:.0f}s)" if r["scriptQC"] else "-"
        vqc = f"{r['videoQC']} ({r['videoRepairRounds']} rep, {r['shots']} shots / {r['shotAttempts']} gens)" if r["videoQC"] else "-"
        final = f"{r['finalSec']:.1f}s" if r["finalSec"] else "-"
        retry = r["metrics"].get("shotRetryRate")
        lines.append(f"| {r['product'] or r['url']} | {r['status']} | {r['audience'] or '-'} | {r['angleFamily'] or '-'} | "
                     f"{r['archetype'] or '-'} | {r['character'] or '-'} | {sqc} | {vqc} | {final} | "
                     f"{retry if retry is not None else '-'} | {r['minutes']} |")
    for r in rows:
        lines += ["", f"## {r['product'] or r['url']}", "", f"- url: {r['url']}", f"- creative: `{r['creativeId']}` · status **{r['status']}**"]
        if r["error"]:
            lines.append(f"- error: {r['error']}")
        why = r["why"]
        if why.get("audience"):
            lines.append(f"- audience: {why['audience']['segment']} [{why['audience']['evidence']}] — {why['audience']['reason']}")
        if why.get("sellingAngle"):
            lines.append(f"- angle: **{why['sellingAngle']['family']}** — {why['sellingAngle']['idea']} ({why['sellingAngle']['whyItWorks']})")
        if why.get("scriptArchetype"):
            lines.append(f"- archetype: {why['scriptArchetype']['archetype']} — {why['scriptArchetype']['reason']}")
        if why.get("character"):
            lines.append(f"- character: {why['character']['name']} [{why['character']['source']}] — {why['character']['whyThisPerson']}")
        if r["script"]:
            lines.append("- script:")
            lines += [f"  - `{b['beatId']}` ({b['type']}) {b['speech']}" for b in r["script"]]
        for w in r["warnings"]:
            lines.append(f"- warning: {w}")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--urls-file", default=None)
    parser.add_argument("--out", default="results")
    parser.add_argument("--duration", type=int, default=None, choices=[15, 30])
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    urls = list(args.urls)
    if args.urls_file:
        urls += [l.strip() for l in Path(args.urls_file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    if not urls:
        parser.error("no URLs given")
    settings = VidaiSettings.from_env()
    settings.mock = settings.mock or args.mock
    if args.data_dir:
        settings.data_dir = Path(args.data_dir).expanduser()
    if not settings.mock and not settings.llm_api_key:
        print("error: VIDAI_LLM_API_KEY is empty (set it in agent/.env) — or pass --mock", file=sys.stderr)
        return 2
    out = Path(args.out) / time.strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    print(f"batch of {len(urls)} -> {out}")
    for index, url in enumerate(urls, start=1):
        started = time.time()
        plan = new_plan(settings, url, mode="auto", duration=args.duration)
        print(f"\n[{index}/{len(urls)}] {url}  creative {plan.creativeId}")
        error = None
        run_dir = None
        try:
            plan, run_dir, _ = await run_creative(plan, settings)
        except Exception as exc:  # the pipeline normally converts failures into FAILED; this is the last net
            logging.exception("batch item crashed")
            error = f"{type(exc).__name__}: {exc}"
            plan.status = Status.FAILED
        final_s = None
        slug = f"{index:02d}_{slugify_hint(plan.product.productName if plan.product else url)}"
        if plan.finalVideo and Path(plan.finalVideo).is_file():
            dest = out / f"{slug}.mp4"
            shutil.copy2(plan.finalVideo, dest)
            final_s = ffprobe_metadata(dest)["duration_s"]
        (out / f"{slug}.plan.json").write_text(plan.to_json(), encoding="utf-8")
        if run_dir and (run_dir / "trace.jsonl").is_file():
            shutil.copy2(run_dir / "trace.jsonl", out / f"{slug}.trace.jsonl")
        rows.append(_row(plan, url, (time.time() - started) / 60, final_s, error))
        _write_summary(out, rows, settings)
        r = rows[-1]
        print(f"    -> {r['status']}  angle={r['angleFamily']} archetype={r['archetype']} character={r['character']} "
              f"scriptQC={r['scriptQC']}/{r['scriptRepairRounds']} videoQC={r['videoQC']}/{r['videoRepairRounds']} "
              f"final={final_s and f'{final_s:.1f}s'}  {r['minutes']} min")
        if r["error"]:
            print(f"    error: {r['error']}")
    ready = sum(1 for r in rows if r["status"] == "READY")
    print(f"\ndone: {ready}/{len(rows)} READY -> {out / 'summary.md'}")
    return 0 if ready == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
