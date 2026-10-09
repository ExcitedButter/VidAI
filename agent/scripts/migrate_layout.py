"""Migrate creatives written by the old flat layout (stages/, clips/, frames/, trace.jsonl, ...) to the
numbered per-step folders described in vidai/agent/run_context.py, then remove process logs.

    python scripts/migrate_layout.py [--data-dir data] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidai.agent.run_context import STAGE_DIRS  # noqa: E402

CLIP_RE = re.compile(r"^(shot_\d+)_a(\d+)\.mp4$")


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _rewrite_paths(obj, mapping: dict[str, str]):
    if isinstance(obj, str):
        for old, new in mapping.items():
            if obj.startswith(old):
                return new + obj[len(old):]
        return obj
    if isinstance(obj, list):
        return [_rewrite_paths(v, mapping) for v in obj]
    if isinstance(obj, dict):
        return {k: _rewrite_paths(v, mapping) for k, v in obj.items()}
    return obj


def migrate(run_dir: Path, dry: bool) -> None:
    run_dir = run_dir.resolve()   # plan.json stores absolute paths; the mapping must use the same form
    plan_path = run_dir / "plan.json"
    if not plan_path.is_file() or not any((run_dir / d).exists() for d in ("stages", "clips", "frames", "trace.jsonl")):
        return
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    d = {k: run_dir / v for k, v in STAGE_DIRS.items()}
    print(f"== {run_dir}")
    if dry:
        return
    mapping: dict[str, str] = {}
    # 01 product
    if (run_dir / "product").is_dir():
        shutil.move(str(run_dir / "product"), str(d["product"]))
        mapping[str(run_dir / "product")] = str(d["product"])
    if plan.get("product"):
        _dump(d["product"] / "product_intelligence.json", plan["product"])
    # 02-05
    _dump(d["audience"] / "audience_candidates.json", {"recommendedIndex": plan.get("audienceIndex"), "candidates": plan.get("audienceCandidates", [])})
    _dump(d["angles"] / "angle_candidates.json", {"recommendedIndex": plan.get("angleIndex"), "candidates": plan.get("angleCandidates", [])})
    _dump(d["character"] / "character_candidates.json", {"recommendedIndex": plan.get("characterIndex"), "candidates": plan.get("characterCandidates", [])})
    if plan.get("characterBrief"):
        _dump(d["character"] / "character_brief.json", plan["characterBrief"])
    if (run_dir / "character_reference.jpg").is_file():
        shutil.move(str(run_dir / "character_reference.jpg"), str(d["character"] / "character_reference.jpg"))
        mapping[str(run_dir / "character_reference.jpg")] = str(d["character"] / "character_reference.jpg")
    if plan.get("scriptStrategy"):
        _dump(d["strategy"] / "script_strategy.json", plan["scriptStrategy"])
    # 06-07
    if plan.get("beats"):
        _dump(d["script"] / "beats.json", plan["beats"])
    if plan.get("speechVisualBeats"):
        _dump(d["script"] / "speech_visual_script.json", {"estimatedDurationSec": round(sum(b.get("estimatedDurationSec", 0) for b in plan["speechVisualBeats"]), 2), "beats": plan["speechVisualBeats"]})
    if plan.get("qc", {}).get("script"):
        _dump(d["script_qc"] / "script_qc.json", plan["qc"]["script"])
    # 08
    if plan.get("shotPlan"):
        _dump(d["shots"] / "shot_plan.json", plan["shotPlan"])
    if (run_dir / "product_cutaway.jpg").is_file():
        d["shots"].mkdir(parents=True, exist_ok=True)
        shutil.move(str(run_dir / "product_cutaway.jpg"), str(d["shots"] / "product_cutaway.jpg"))
    # 09 clips
    clips_dir = run_dir / "clips"
    if clips_dir.is_dir():
        d["video"].mkdir(parents=True, exist_ok=True)
        (d["final"] / "normalized").mkdir(parents=True, exist_ok=True)
        for clip in sorted(clips_dir.iterdir()):
            m = CLIP_RE.match(clip.name)
            if m:
                new = d["video"] / f"{m.group(1)}_attempt_{int(m.group(2)):02d}.mp4"
                shutil.move(str(clip), str(new)); mapping[str(clip)] = str(new)
            elif clip.name.endswith("_norm.mp4"):
                shutil.move(str(clip), str(d["final"] / "normalized" / clip.name.replace("_norm", "")))
            else:
                shutil.move(str(clip), str(d["video"] / clip.name))
        shutil.rmtree(clips_dir, ignore_errors=True)
    # 10 QC frames + verdicts (verdicts recovered from the trace before it is deleted)
    trace = []
    if (run_dir / "trace.jsonl").is_file():
        for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                trace.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    verdicts = [e for e in trace if e.get("kind") == "shot_qc"]
    replies = [e for e in trace if e.get("kind") == "module_reply" and e.get("module") == "video_qc"]
    per_shot: dict[str, list] = {}
    for e in verdicts:
        per_shot.setdefault(e["shot"], []).append(e)
    frames_dir = run_dir / "frames"
    if frames_dir.is_dir():
        d["video_qc"].mkdir(parents=True, exist_ok=True)
        for fd in sorted(frames_dir.iterdir()):
            m = CLIP_RE.match(fd.name + ".mp4")
            new_name = f"{m.group(1)}_attempt_{int(m.group(2)):02d}" if m else fd.name
            shutil.move(str(fd), str(d["video_qc"] / new_name))
        shutil.rmtree(frames_dir, ignore_errors=True)
    for shot_id, events in per_shot.items():
        dirs = sorted(p for p in d["video_qc"].glob(f"{shot_id}_attempt_*") if p.is_dir())
        for index, event in enumerate(events):
            target = dirs[index] if index < len(dirs) else d["video_qc"] / f"{shot_id}_qc_{index + 1}"
            target.mkdir(parents=True, exist_ok=True)
            reply = None
            if index < len(replies):
                pass
            _dump(target / "verdict.json", {"status": event.get("status"), "repairAction": event.get("action"), "reason": event.get("reason")})
    for shot in plan.get("shotPlan", []):
        if shot.get("qc") and shot.get("clipPath"):
            new_clip = _rewrite_paths(shot["clipPath"], mapping)
            m = CLIP_RE.match(Path(shot["clipPath"]).name)
            stem = f"{m.group(1)}_attempt_{int(m.group(2)):02d}" if m else Path(new_clip).stem
            _dump(d["video_qc"] / stem / "verdict.json", {"verdict": shot["qc"], "clip": new_clip})
    if plan.get("qc", {}).get("video"):
        _dump(d["video_qc"] / "video_qc.json", plan["qc"]["video"])
    metrics = run_dir.parents[2] / "records" / f"{run_dir.parent.name}_{run_dir.name}" / "metrics.json"
    if metrics.is_file():
        d["final"].mkdir(parents=True, exist_ok=True)
        shutil.copy2(metrics, d["final"] / "metrics.json")
    # 11 final
    if (run_dir / "final.mp4").is_file():
        d["final"].mkdir(parents=True, exist_ok=True)
        shutil.move(str(run_dir / "final.mp4"), str(d["final"] / "final.mp4"))
        mapping[str(run_dir / "final.mp4")] = str(d["final"] / "final.mp4")
    _dump(d["final"] / "why_this_creative.json", {
        "product": (plan.get("product") or {}).get("productName"),
        "audience": plan.get("audience"), "sellingAngle": plan.get("sellingAngle"),
        "scriptArchetype": plan.get("scriptStrategy"), "character": plan.get("character"),
        "userOverrides": plan.get("userOverrides"), "warnings": plan.get("warnings")})
    # usage from trace (so cost survives the log deletion)
    usage = {"llmCalls": 0, "promptTokens": 0, "completionTokens": 0}
    for e in trace:
        u = e.get("usage") if e.get("kind") == "module_reply" else None
        if u:
            usage["llmCalls"] += 1; usage["promptTokens"] += int(u.get("prompt_tokens") or 0); usage["completionTokens"] += int(u.get("completion_tokens") or 0)
    plan["usage"] = usage
    plan = _rewrite_paths(plan, mapping)
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    # process logs / temp folders
    for name in ("stages", "identity", "review", "final.txt", "trace.jsonl"):
        target = run_dir / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
    print("   migrated:", ", ".join(sorted(p.name for p in run_dir.iterdir())))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.data_dir)
    for run_dir in sorted(root.glob("creatives/*/v*")):
        migrate(run_dir, args.dry_run)
    for trace in root.glob("records/*/trace.jsonl"):
        print("rm", trace)
        if not args.dry_run:
            trace.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
