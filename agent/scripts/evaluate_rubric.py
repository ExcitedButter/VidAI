"""Score finished creatives on the PRD §21.1 rubric with an LLM judge (frames + Creative Plan).

    python scripts/evaluate_rubric.py cr_ab12cd34 [cr_...|path/to/plan.json ...] [--out results/rubric] [--mock]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidai.agent.media import extract_frames, ffprobe_metadata  # noqa: E402
from vidai.agent.prompts import load_prompt  # noqa: E402
from vidai.agent.single_run import build_clients, load_plan  # noqa: E402
from vidai.config import VidaiSettings  # noqa: E402
from vidai.plan import CreativePlan  # noqa: E402

DIMENSIONS = ("audienceFit", "sellingAngleStrength", "characterFit", "naturalness",
              "productIntegration", "continuity", "overallPublishability")


def _payload(plan: CreativePlan) -> dict[str, Any]:
    character = plan.character
    return {
        "product": plan.product.model_dump(include={"productName", "brandName", "category", "features", "benefits",
                                                     "claims", "proofPoints", "riskFlags"}) if plan.product else None,
        "audience": plan.audience, "sellingAngle": plan.sellingAngle, "scriptStrategy": plan.scriptStrategy,
        "character": {"name": character.name, "source": character.source, "ageRange": character.ageRange,
                      "continuityAnchors": character.continuityAnchors} if character else None,
        "speechVisualBeats": plan.speechVisualBeats,
        "shotPlan": [{"shotId": s.shotId, "beatIds": s.beatIds, "visualType": s.visualType,
                      "productVisible": s.productVisible, "attempts": s.attempts,
                      "qc": s.qc.status if s.qc else None} for s in plan.shotPlan],
        "warnings": plan.warnings,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("creatives", nargs="+", help="creativeId or plan.json path")
    parser.add_argument("--out", default="results/rubric")
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    settings = VidaiSettings.from_env()
    settings.mock = settings.mock or args.mock
    if args.data_dir:
        settings.data_dir = Path(args.data_dir).expanduser()
    out = Path(args.out) / time.strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    llm, _, seedance = build_clients(settings)
    rows: list[dict[str, Any]] = []
    try:
        for ref in args.creatives:
            plan, path = load_plan(settings, ref)
            if not plan.finalVideo or not Path(plan.finalVideo).is_file():
                print(f"{plan.creativeId}: no final video, skipped")
                continue
            meta = ffprobe_metadata(Path(plan.finalVideo))
            frames = extract_frames(Path(plan.finalVideo), out / f"{plan.creativeId}_frames", args.frames, meta["duration_s"])
            reply = await llm.complete_json(load_prompt("rubric_eval"), json.dumps(_payload(plan), default=str),
                                            purpose="rubric_eval", image_paths=frames)
            scores = reply.get("scores", {})
            row = {"creativeId": plan.creativeId, "product": plan.product.productName if plan.product else None,
                   "scores": {d: scores.get(d, {}).get("score") for d in DIMENSIONS},
                   "reasons": {d: scores.get(d, {}) for d in DIMENSIONS},
                   "lowestDimension": reply.get("lowestDimension"), "summary": reply.get("summary", "")}
            values = [v for v in row["scores"].values() if isinstance(v, (int, float))]
            row["mean"] = round(sum(values) / len(values), 2) if values else None
            rows.append(row)
            print(f"{plan.creativeId} {row['product']}: mean {row['mean']}  {row['scores']}")
    finally:
        await llm.close()
        await seedance.close()
    (out / "rubric.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    header = "| creative | product | " + " | ".join(DIMENSIONS) + " | mean | lowest |"
    lines = ["# Rubric evaluation (PRD §21.1, LLM judge)", "", header, "|" + "---|" * (len(DIMENSIONS) + 4)]
    for r in rows:
        lines.append(f"| {r['creativeId']} | {r['product']} | " + " | ".join(str(r['scores'][d]) for d in DIMENSIONS)
                     + f" | {r['mean']} | {r['lowestDimension']} |")
    for r in rows:
        lines += ["", f"## {r['product']} ({r['creativeId']})", "", r["summary"]]
        lines += [f"- {d}: {r['reasons'][d].get('score')} — {r['reasons'][d].get('reason', '')} "
                  f"(layer: {r['reasons'][d].get('planLayer', '')})" for d in DIMENSIONS]
    (out / "rubric.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"-> {out / 'rubric.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
