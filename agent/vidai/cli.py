"""MasSurge CLI: product URL -> talking-head creative, with guided pause points and revisions.

    python -m vidai.cli generate --url https://shop.example/products/x [--mode guided|auto] [--mock]
    python -m vidai.cli resume   cr_ab12cd34 --select-angle angle_xxx
    python -m vidai.cli revise   cr_ab12cd34 --character male_home_cook_01
    python -m vidai.cli show     cr_ab12cd34
    python -m vidai.cli list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from vidai.agent.harness import prepare_revision, resume_group
from vidai.agent.run_context import PipelineHalt
from vidai.agent.single_run import load_plan, new_plan, run_creative
from vidai.config import VidaiSettings
from vidai.plan import CreativePlan, Status
from vidai.storage import DataLayout

STOP_GROUP = {"strategy": "strategy", "character": "character", "script": "script_qc"}


# ---------------------------------------------------------------------------- parser
def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", default=None, help="Override data root")
    parser.add_argument("--mock", action="store_true", help="Offline run: canned LLM replies + ffmpeg 'Seedance'")
    parser.add_argument("--interactive", action="store_true",
                        help="Guided mode: stop at each pause point and choose in the terminal")
    parser.add_argument("--stop-at", choices=sorted(STOP_GROUP), default=None,
                        help="Halt after this step (STRATEGY_READY / CHARACTER_READY / SCRIPT_READY); resume later")
    parser.add_argument("-v", "--verbose", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vidai", description="MasSurge MVP creative pipeline (PRD v1.0)")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Create a creative from a product URL")
    gen.add_argument("--url", required=True, help="Product page URL (or a local HTML file for offline runs)")
    gen.add_argument("--mode", choices=["guided", "auto"], default=None)
    gen.add_argument("--duration", type=int, choices=[15, 30], default=None)
    gen.add_argument("--aspect", default=None, help="9:16 (default), 16:9, 1:1 ...")
    gen.add_argument("--resolution", default=None, help="720p (default) or 1080p")
    gen.add_argument("--product-text", default=None,
                     help="Fallback product description (text, or @path to a file) when the page cannot be read")
    gen.add_argument("--product-image", default=None, help="Local hero image of the product")
    gen.add_argument("--new-character", action="store_true",
                     help="Generate a character from a brief instead of using the curated library")
    _common(gen)

    res = sub.add_parser("resume", help="Continue a halted / failed creative, optionally applying a choice")
    res.add_argument("creative", help="creativeId or path to plan.json")
    res.add_argument("--select-angle", default=None, help="angleId to use (at STRATEGY_READY)")
    res.add_argument("--select-character", default=None, help="characterId to use (at CHARACTER_READY)")
    res.add_argument("--script-json", default=None, help="Edited script JSON (at SCRIPT_READY)")
    _common(res)

    rev = sub.add_parser("revise", help="Change one decision on a finished creative; only downstream steps re-run")
    rev.add_argument("creative", help="creativeId or path to plan.json")
    rev.add_argument("--angle", default=None, help="angleId")
    rev.add_argument("--character", default=None, help="characterId")
    rev.add_argument("--script-json", default=None, help="Edited script JSON ({speechVisualBeats:[...]} or a list)")
    rev.add_argument("--regenerate-shot", action="append", default=None, metavar="SHOT_ID")
    _common(rev)

    show = sub.add_parser("show", help="Print a creative plan")
    show.add_argument("creative")
    show.add_argument("--json", action="store_true", help="Dump the full plan JSON")
    show.add_argument("--data-dir", default=None)

    lst = sub.add_parser("list", help="List finished creatives")
    lst.add_argument("--data-dir", default=None)
    return parser


# ---------------------------------------------------------------------------- helpers
def _settings(args: argparse.Namespace) -> VidaiSettings:
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = VidaiSettings.from_env()
    if getattr(args, "data_dir", None):
        settings.data_dir = Path(args.data_dir).expanduser()
    if getattr(args, "mock", False):
        settings.mock = True
    return settings


def _read_text_arg(value: Optional[str]) -> str:
    if not value:
        return ""
    if value.startswith("@"):
        return Path(value[1:]).expanduser().read_text(encoding="utf-8")
    return value


def _load_script_json(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("speechVisualBeats", [])
    if not isinstance(data, list):
        raise SystemExit("script JSON must be a list of beats or {speechVisualBeats: [...]}")
    return data


def _interactive_pause(plan: CreativePlan, group: str) -> None:
    """Terminal version of the Guided-mode UI (PRD §12)."""
    if group == "strategy":
        audience = plan.audience
        if audience:
            print(f"\naudience: {audience.segmentName} [{audience.evidence}] - {audience.reason}")
        print("selling angles (recommended first):")
        for index, angle in enumerate(plan.angleCandidates, start=1):
            print(f"  {index}. [{angle.angleFamily}] {angle.oneLineIdea}  (score {angle.score:.2f})")
            print(f"       why: {angle.whyItWorks}")
        choice = input("pick angle number [Enter = recommended, q = stop here]: ").strip().lower()
        if choice == "q":
            raise PipelineHalt()
        if choice.isdigit() and 1 <= int(choice) <= len(plan.angleCandidates):
            plan.select_angle(plan.angleCandidates[int(choice) - 1].angleId, by_user=True)
    elif group == "character":
        print("\ncharacters (recommended first):")
        for index, character in enumerate(plan.characterCandidates, start=1):
            print(f"  {index}. {character.name} [{character.source}] match {character.matchScore:.2f}")
            print(f"       {character.whyThisPerson}")
        choice = input("pick character number [Enter = recommended, q = stop here]: ").strip().lower()
        if choice == "q":
            raise PipelineHalt()
        if choice.isdigit() and 1 <= int(choice) <= len(plan.characterCandidates):
            plan.select_character(plan.characterCandidates[int(choice) - 1].characterId, by_user=True)
    elif group == "script_qc":
        qc = plan.script_qc
        print(f"\nscript ({qc.estimatedDurationSec:.1f}s estimated, QC {qc.overall}, "
              f"{qc.repairRounds} repair round(s)):" if qc else "\nscript:")
        for beat in plan.speechVisualBeats:
            flag = ", product" if beat.visual.productRequired else ""
            print(f"  [{beat.beatId}] ({beat.visual.type}{flag}) {beat.speech}")
        choice = input("Enter = accept, path to edited script JSON = replace, q = stop here: ").strip()
        if choice.lower() == "q":
            raise PipelineHalt()
        if choice:
            from vidai.plan import SpeechVisualBeat

            plan.speechVisualBeats = [SpeechVisualBeat.model_validate(b) for b in _load_script_json(choice)]
            plan.userOverrides["pendingScriptQC"] = True


def _make_pause(args: argparse.Namespace):
    if not getattr(args, "interactive", False):
        return None
    return _interactive_pause


def _print_plan(plan: CreativePlan, plan_path: Path, record_dir: Optional[Path] = None) -> None:
    print(f"\ncreative {plan.creativeId} v{plan.version}   status {plan.status.value}   mode {plan.mode}")
    if plan.error:
        print(f"error: {plan.error}")
    if plan.product:
        print(f"product: {plan.product.productName} ({plan.product.brandName or 'brand n/a'}) "
              f"confidence {plan.product.confidence:.2f}")
    if plan.audience:
        audience = plan.audience
        print(f"audience: {audience.segmentName} [{audience.evidence}] - {audience.reason}")
    if plan.angleCandidates:
        print("angles:")
        for index, angle in enumerate(plan.angleCandidates):
            mark = "*" if index == plan.angleIndex else " "
            print(f"  {mark} {angle.angleId}  {angle.angleFamily:<22} {angle.score:.2f}  {angle.oneLineIdea}")
    if plan.scriptStrategy:
        print(f"archetype: {plan.scriptStrategy.archetype} - {plan.scriptStrategy.reason}")
    if plan.characterCandidates:
        print("characters:")
        for index, character in enumerate(plan.characterCandidates):
            mark = "*" if index == plan.characterIndex else " "
            print(f"  {mark} {character.characterId:<30} {character.matchScore:.2f}  {character.name}")
    if plan.speechVisualBeats:
        qc = plan.script_qc
        header = (f"script ({qc.estimatedDurationSec:.1f}s est., QC {qc.overall}, {qc.repairRounds} repair round(s)):"
                  if qc else "script:")
        print(header)
        for beat in plan.speechVisualBeats:
            flag = ", product" if beat.visual.productRequired else ""
            print(f"  [{beat.beatId}] ({beat.visual.type}{flag}) {beat.speech}")
    if plan.shotPlan:
        vqc = plan.video_qc
        print(f"shots (video QC {vqc.overall}, {vqc.repairRounds} repair round(s)):" if vqc else "shots:")
        for shot in plan.shotPlan:
            qc_text = f"qc={shot.qc.status}" if shot.qc else ""
            print(f"  {shot.shotId} {shot.durationSec:4.1f}s {shot.visualType:<20} {shot.status:<9} "
                  f"attempts={shot.attempts} {qc_text}")
    for warning in plan.warnings:
        print(f"warning: {warning}")
    print(f"plan: {plan_path}")
    if plan.finalVideo:
        print(f"final video: {plan.finalVideo}")
    if record_dir:
        print(f"record: {record_dir}")


def _exit_code(plan: CreativePlan) -> int:
    if plan.status == Status.READY:
        return 0
    if plan.status == Status.FAILED:
        return 1
    return 3   # halted at a pause point / stop-at


# ---------------------------------------------------------------------------- commands
def _cmd_generate(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if args.aspect:
        settings.aspect_ratio = args.aspect
    if args.resolution:
        settings.resolution = args.resolution
    plan = new_plan(settings, args.url, mode=args.mode, duration=args.duration,
                    product_text=_read_text_arg(args.product_text), product_image=args.product_image,
                    new_character=args.new_character)
    plan, run_dir, record_dir = asyncio.run(run_creative(
        plan, settings, stop_after=STOP_GROUP.get(args.stop_at or ""), on_pause=_make_pause(args)))
    _print_plan(plan, run_dir / "plan.json", record_dir)
    if plan.status not in (Status.READY, Status.FAILED):
        print(f"\nhalted at {plan.status.value}; continue with: python -m vidai.cli resume {plan.creativeId} "
              f"[--select-angle ID | --select-character ID | --script-json FILE]")
    return _exit_code(plan)


def _cmd_resume(args: argparse.Namespace) -> int:
    settings = _settings(args)
    plan, _ = load_plan(settings, args.creative)
    if args.select_angle:
        plan.select_angle(args.select_angle, by_user=True)
    if args.select_character:
        plan.select_character(args.select_character, by_user=True)
    start = resume_group(plan)
    if args.script_json:
        from vidai.plan import SpeechVisualBeat

        plan.speechVisualBeats = [SpeechVisualBeat.model_validate(b) for b in _load_script_json(args.script_json)]
        plan.userOverrides["script"] = f"edited at v{plan.version}"
        start = "script_qc"
    if start is None:
        print(f"creative {plan.creativeId} is already READY; use `revise` to change a decision")
        return 0
    plan, run_dir, record_dir = asyncio.run(run_creative(
        plan, settings, start_group=start, stop_after=STOP_GROUP.get(args.stop_at or ""), on_pause=_make_pause(args)))
    _print_plan(plan, run_dir / "plan.json", record_dir)
    return _exit_code(plan)


def _cmd_revise(args: argparse.Namespace) -> int:
    settings = _settings(args)
    plan, _ = load_plan(settings, args.creative)
    try:
        start = prepare_revision(
            plan, angle_id=args.angle, character_id=args.character,
            script_beats=_load_script_json(args.script_json) if args.script_json else None,
            shot_ids=args.regenerate_shot,
        )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    plan, run_dir, record_dir = asyncio.run(run_creative(
        plan, settings, start_group=start, stop_after=STOP_GROUP.get(args.stop_at or ""), on_pause=_make_pause(args)))
    _print_plan(plan, run_dir / "plan.json", record_dir)
    return _exit_code(plan)


def _cmd_show(args: argparse.Namespace) -> int:
    settings = VidaiSettings.from_env()
    if args.data_dir:
        settings.data_dir = Path(args.data_dir).expanduser()
    plan, path = load_plan(settings, args.creative)
    if args.json:
        print(plan.to_json())
    else:
        _print_plan(plan, path)
        print("why this creative:", json.dumps(plan.why_this_creative(), indent=2, ensure_ascii=False))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    settings = VidaiSettings.from_env()
    if args.data_dir:
        settings.data_dir = Path(args.data_dir).expanduser()
    manifest = DataLayout(settings.data_dir).manifest_path
    if not manifest.is_file():
        print("no records yet")
        return 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        print(f"{row['id']}  [{row['status']}]  {row.get('product') or '?'}  angle={row.get('angle')}  "
              f"character={row.get('character')}  {row.get('final_video') or row.get('error') or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {"generate": _cmd_generate, "resume": _cmd_resume, "revise": _cmd_revise,
                "show": _cmd_show, "list": _cmd_list}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
