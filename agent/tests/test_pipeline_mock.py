"""Offline end-to-end tests: local product page -> full PRD pipeline (mock LLM + ffmpeg Seedance)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from vidai.agent.harness import prepare_revision, resume_group
from vidai.agent.run_context import PipelineHalt
from vidai.agent.single_run import load_plan, new_plan, run_creative
from vidai.agent.stages.script import rule_checks
from vidai.agent.stages.shots import segment_beats
from vidai.config import VidaiSettings
from vidai.plan import CreativePlan, GenerationSettings, SpeechVisualBeat, Status, VisualSpec

PAGE = """<!doctype html><html><head>
<title>AeroBrew Portable Espresso Maker | AeroBrew</title>
<meta name="description" content="Barista-grade espresso anywhere, no electricity.">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",
 "name":"AeroBrew Portable Espresso Maker","brand":{"@type":"Brand","name":"AeroBrew"},
 "description":"Hand-powered 18-bar espresso maker for travel and camping.","image":["hero.jpg"],
 "offers":{"@type":"Offer","price":"89.00","priceCurrency":"USD"},
 "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.8","reviewCount":"1312"}}</script>
</head><body><h1>AeroBrew Espresso Maker</h1><img src="hero.jpg" alt="AeroBrew">
<ul><li>18 bar of pressure from a hand pump, no batteries</li>
<li>Works with ground coffee and capsules</li>
<li>Weighs 340 g and fits a jacket pocket</li><li>Dishwasher safe parts</li></ul>
<p>Over 1,300 five-star reviews from campers and commuters.</p></body></html>"""


def _settings(tmp_path: Path) -> VidaiSettings:
    settings = VidaiSettings.from_env()
    settings.mock = True
    settings.trace = True          # tests inspect the debug trace
    settings.data_dir = tmp_path / "data"
    return settings


@pytest.fixture()
def fixture_page(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    site.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=1:duration=1",
                    "-frames:v", "1", str(site / "hero.jpg")], check=True)
    (site / "page.html").write_text(PAGE, encoding="utf-8")
    return site / "page.html"


def _run(plan, settings, **kwargs):
    return asyncio.run(run_creative(plan, settings, **kwargs))


# --------------------------------------------------------------------------- end to end
def test_auto_mode_end_to_end(tmp_path: Path, fixture_page: Path) -> None:
    settings = _settings(tmp_path)
    plan = new_plan(settings, str(fixture_page), mode="auto", duration=15)
    plan, run_dir, record_dir = _run(plan, settings)

    assert plan.status == Status.READY, plan.error
    # Stage A: deterministic parser + structured intelligence
    assert plan.product and plan.product.productName.startswith("AeroBrew")
    assert plan.product.price and "89" in plan.product.price
    assert plan.product.heroImagePath and Path(plan.product.heroImagePath).is_file()
    # Stages B-D, G
    assert plan.audience and plan.audience.evidence in ("stated", "inferred") and plan.audience.reason
    assert 3 <= len(plan.angleCandidates) <= 5 and plan.sellingAngle is not None
    assert plan.scriptStrategy and plan.scriptStrategy.archetype == "pas_proof"
    assert plan.character and plan.character.whyThisPerson and len(plan.characterCandidates) == 3
    # Stage F: one targeted repair round, then the script fits the duration + clip limits
    qc = plan.script_qc
    assert qc and qc.overall == "pass" and qc.repairRounds == 1
    total = sum(b.estimatedDurationSec for b in plan.speechVisualBeats)
    assert 15 * 0.85 <= total <= 15 * 1.15
    assert all(b.estimatedDurationSec <= 8 for b in plan.speechVisualBeats)
    assert any(b.visual.productRequired for b in plan.speechVisualBeats)
    # Stage I: beats never split, consecutive talking-head beats merged
    assert plan.shotPlan and len(plan.shotPlan) < len(plan.speechVisualBeats)
    assert sum(len(s.beatIds) for s in plan.shotPlan) == len(plan.speechVisualBeats)
    assert all(s.durationSec <= settings.hard_max_shot_s for s in plan.shotPlan)
    assert all(s.prompt for s in plan.shotPlan)
    # Stages J/K: one soft fail -> exactly one shot regenerated with a simplified action
    assert all(s.status == "passed" for s in plan.shotPlan)
    assert plan.video_qc and plan.video_qc.overall == "pass" and plan.video_qc.repairRounds == 1
    assert sum(s.attempts for s in plan.shotPlan) == len(plan.shotPlan) + 1
    repaired = [s for s in plan.shotPlan if s.attempts == 2]
    assert len(repaired) == 1 and "simplify_action" in " ".join(repaired[0].repairHistory)
    # Assembly + persistence
    assert plan.finalVideo and Path(plan.finalVideo).is_file() and Path(plan.finalVideo).stat().st_size > 0
    assert (run_dir / "plan.json").is_file()
    # one clearly named folder per step, each holding that step's own output
    expected = {
        "01_product_intelligence": ["scraped.json", "product_intelligence.json"],
        "02_audience": ["audience_candidates.json"], "03_selling_angles": ["angle_candidates.json"],
        "04_character": ["character_candidates.json", "character_reference.jpg"],
        "05_script_strategy": ["script_strategy.json"], "06_script": ["beats.json", "speech_visual_script.json"],
        "07_script_qc": ["script_qc.json", "round_0_qc.json", "round_1_qc.json"], "08_shot_plan": ["shot_plan.json"],
        "10_video_qc": ["video_qc.json"], "11_final": ["final.mp4", "why_this_creative.json", "metrics.json"],
    }
    for folder, files in expected.items():
        for name in files:
            assert (run_dir / folder / name).is_file(), f"{folder}/{name}"
    clips = sorted(p.name for p in (run_dir / "09_video_generation").glob("shot_*_attempt_*.mp4"))
    assert len(clips) == len(plan.shotPlan) + 1 and clips[0] == "shot_01_attempt_01.mp4"
    assert all((run_dir / "10_video_qc" / Path(c).stem / "verdict.json").is_file() for c in clips)
    assert not (run_dir / "stages").exists() and not (run_dir / "trace.jsonl").exists()
    assert plan.usage["llmCalls"] > 0
    statuses = {json.loads(line)["status"] for line in (run_dir / "_trace.jsonl").read_text().splitlines()}
    for status in ("ANALYZING_PRODUCT", "STRATEGY_READY", "CHARACTER_READY", "SCRIPT_GENERATING", "SCRIPT_QC",
                   "SCRIPT_READY", "SHOT_PLANNING", "VIDEO_GENERATING", "VIDEO_QC", "REPAIRING", "READY"):
        assert status in statuses, status
    assert record_dir and (record_dir / "final.mp4").is_file() and (record_dir / "why_this_creative.json").is_file()
    # PRD §11.4 / §14.3: one identity source per creative — the first clip's frame feeds every later shot
    assert plan.character.visualReferenceAssets and Path(plan.character.visualReferenceAssets[0]).is_file()
    events = [json.loads(line) for line in (run_dir / "_trace.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "identity_reference" for e in events)
    generated = [e for e in events if e["kind"] == "shot_generated"]
    assert generated[0]["first_frame"] == "" and all(e["first_frame"] for e in generated[1:])
    # PRD §14-15: the talking head has an audible speech track and the final video keeps it
    from vidai.agent.media import ffprobe_metadata
    assert ffprobe_metadata(Path(plan.finalVideo))["has_audio"]
    assert all(s.qc.checks["audio"].status == "pass" for s in plan.shotPlan)
    # PRD §21 metrics are recorded
    metrics = json.loads((record_dir / "metrics.json").read_text())
    assert metrics["generationSucceededWithoutIntervention"] is True and metrics["shots"] == len(plan.shotPlan)
    assert metrics["shotRetryRate"] == round(1 / len(plan.shotPlan), 3)
    rows = [json.loads(l) for l in (settings.data_dir / "records_manifest.jsonl").read_text().splitlines() if l.strip()]
    assert rows[-1]["creativeId"] == plan.creativeId and rows[-1]["status"] == "READY"
    why = plan.why_this_creative()
    assert why["sellingAngle"]["family"] and why["character"]["whyThisPerson"]


def test_guided_pauses_accept_overrides(tmp_path: Path, fixture_page: Path) -> None:
    settings = _settings(tmp_path)
    seen: list[str] = []

    def on_pause(plan: CreativePlan, group: str) -> None:
        seen.append(group)
        if group == "strategy":
            plan.select_angle(plan.angleCandidates[1].angleId, by_user=True)
        elif group == "character":
            plan.select_character(plan.characterCandidates[-1].characterId, by_user=True)

    plan = new_plan(settings, str(fixture_page), mode="guided")
    plan, _, _ = _run(plan, settings, on_pause=on_pause)
    assert seen == ["strategy", "character", "script_qc"]
    assert plan.status == Status.READY, plan.error
    assert plan.userOverrides["angle"] == plan.sellingAngle.angleId == plan.angleCandidates[1].angleId
    assert plan.userOverrides["character"] == plan.character.characterId
    assert plan.scriptStrategy.archetype == "desired_life_bridge"   # router followed the overridden angle


def test_halt_and_resume(tmp_path: Path, fixture_page: Path) -> None:
    settings = _settings(tmp_path)

    def halt(plan: CreativePlan, group: str) -> None:
        raise PipelineHalt()

    plan = new_plan(settings, str(fixture_page), mode="guided")
    plan, run_dir, record_dir = _run(plan, settings, on_pause=halt)
    assert plan.status == Status.STRATEGY_READY and record_dir is None and plan.finalVideo is None

    loaded, path = load_plan(settings, plan.creativeId)
    assert path == run_dir / "plan.json" and loaded.status == Status.STRATEGY_READY
    assert resume_group(loaded) == "character"
    loaded.select_angle(loaded.angleCandidates[2].angleId, by_user=True)
    loaded, run_dir2, record_dir2 = _run(loaded, settings, start_group="character")
    assert loaded.status == Status.READY, loaded.error
    assert loaded.version == 1 and run_dir2 == run_dir and record_dir2 is not None
    assert loaded.sellingAngle.angleFamily == "personal_discovery"
    assert loaded.scriptStrategy.archetype == "personal_discovery"


def test_revise_angle_reruns_downstream_only(tmp_path: Path, fixture_page: Path) -> None:
    settings = _settings(tmp_path)
    plan = new_plan(settings, str(fixture_page), mode="auto")
    plan, _, _ = _run(plan, settings)
    assert plan.status == Status.READY, plan.error
    product_before = plan.product.model_dump()
    history_before = len(plan.history)

    start = prepare_revision(plan, angle_id=plan.angleCandidates[1].angleId)
    assert start == "character" and plan.version == 2 and plan.shotPlan == [] and plan.finalVideo is None
    plan, run_dir, record_dir = _run(plan, settings, start_group=start)
    assert plan.status == Status.READY, plan.error
    assert run_dir.name == "v02" and record_dir and record_dir.name.endswith("_v02")
    assert plan.product.model_dump() == product_before
    new_stages = [e.stage for e in plan.history[history_before:]]
    assert "product_parser" not in new_stages and "audience_analyst" not in new_stages
    assert new_stages[0] == "character_matcher" and new_stages[-1] == "assembler"


def test_regenerate_single_shot(tmp_path: Path, fixture_page: Path) -> None:
    settings = _settings(tmp_path)
    plan = new_plan(settings, str(fixture_page), mode="auto")
    plan, _, _ = _run(plan, settings)
    assert plan.status == Status.READY, plan.error
    attempts_before = {s.shotId: s.attempts for s in plan.shotPlan}
    target = plan.shotPlan[-1].shotId

    start = prepare_revision(plan, shot_ids=[target])
    assert start == "video"
    plan, _, _ = _run(plan, settings, start_group=start)
    assert plan.status == Status.READY, plan.error
    # Only the requested shot is touched (the fresh mock judge soft-fails its first QC call once more,
    # so the regenerated shot may take one repair attempt); every other shot keeps its clip.
    for shot in plan.shotPlan:
        if shot.shotId == target:
            assert shot.attempts > attempts_before[shot.shotId]
            assert any("user requested regeneration" in note for note in shot.repairHistory)
        else:
            assert shot.attempts == attempts_before[shot.shotId], shot.shotId
    assert plan.userOverrides["regeneratedShots"] == [target]
    assert plan.finalVideo and Path(plan.finalVideo).is_file()


def test_unreachable_page_requires_product_text(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    missing = str(tmp_path / "missing.html")
    plan = new_plan(settings, missing, mode="auto")
    plan, _, record_dir = _run(plan, settings, stop_after="product")
    assert plan.status == Status.FAILED and "--product-text" in (plan.error or "")
    assert record_dir is not None   # failures are recorded too

    plan2 = new_plan(settings, missing, mode="auto",
                     product_text="AeroBrew hand-powered espresso maker. 18 bar pressure, 340 g, works with grounds or capsules. $89.")
    plan2, _, _ = _run(plan2, settings, stop_after="product")
    assert plan2.status == Status.ANALYZING_PRODUCT and plan2.product is not None
    assert any("unreachable" in note for note in plan2.product.sourceNotes) or plan2.warnings


# --------------------------------------------------------------------------- unit: rules
def _beat(beat_id: str, seconds: float, vtype: str = "talking_head", product: bool = False) -> SpeechVisualBeat:
    return SpeechVisualBeat(beatId=beat_id, speech="x", estimatedDurationSec=seconds,
                            visual=VisualSpec(type=vtype, productRequired=product))


def test_segment_beats_merges_and_never_splits() -> None:
    beats = [_beat("b1", 3), _beat("b2", 4), _beat("b3", 3, "hold_product", True), _beat("b4", 9), _beat("b5", 2)]
    shots, warnings = segment_beats(beats, max_shot_s=8, min_shot_s=2, hard_cap_s=10)
    assert [s.beatIds for s in shots] == [["b1", "b2"], ["b3"], ["b4"], ["b5"]]
    assert shots[0].durationSec == 7 and shots[0].visualType == "talking_head" and not shots[0].productVisible
    assert shots[1].productVisible and shots[1].visualType == "hold_product"
    assert shots[2].durationSec == 9 and warnings and "b4" in warnings[0]
    assert all(s.durationSec <= 10 for s in shots)


def test_rule_checks_flag_long_script_and_late_product() -> None:
    plan = CreativePlan(generationSettings=GenerationSettings(targetDurationSec=15))
    plan.speechVisualBeats = [_beat("b1", 6), _beat("b2", 6), _beat("b3", 6), _beat("b4", 6, "hold_product", True)]
    checks, instructions, failed = rule_checks(plan)
    assert checks["durationFit"].status == "fail" and any("Reduce" in i for i in instructions)
    assert checks["productTiming"].status == "fail" and "b4" in failed
    plan.speechVisualBeats = [_beat("b1", 3), _beat("b2", 4, "hold_product", True), _beat("b3", 4), _beat("b4", 4)]
    checks, instructions, failed = rule_checks(plan)
    assert all(c.status == "pass" for c in checks.values()) and not instructions and not failed


def test_regenerate_beats_then_requalify(tmp_path: Path, fixture_page: Path) -> None:
    settings = _settings(tmp_path)
    plan = new_plan(settings, str(fixture_page), mode="auto")
    plan, _, _ = _run(plan, settings)
    assert plan.status == Status.READY, plan.error
    start = prepare_revision(plan, regenerate_beats=["b2"])
    assert start == "script_qc" and plan.shotPlan == [] and plan.speechVisualBeats
    plan, run_dir, _ = _run(plan, settings, start_group=start)
    assert plan.status == Status.READY, plan.error
    assert plan.userOverrides["regeneratedBeats"] == ["b2"] and "regenerateBeats" not in plan.userOverrides
    modules = [json.loads(l)["module"] for l in (run_dir / "_trace.jsonl").read_text().splitlines()
               if json.loads(l)["kind"] == "module_reply"]
    assert modules[0] == "script_repair" and "beat_planner" not in modules
    assert plan.version == 2 and Path(plan.finalVideo).is_file()


def test_guided_product_confirmation_pause(tmp_path: Path) -> None:
    """PRD §4.4: thin product data pauses guided mode so the user can confirm or add a description."""
    settings = _settings(tmp_path)
    seen: list[str] = []

    def on_pause(plan: CreativePlan, group: str) -> None:
        seen.append(group)
        raise PipelineHalt()

    plan = new_plan(settings, str(tmp_path / "missing.html"), mode="guided",
                    product_text="A hand-powered espresso maker for travel.")
    plan, _, _ = _run(plan, settings, on_pause=on_pause)
    assert seen == ["product"] and plan.status == Status.ANALYZING_PRODUCT
    assert plan.product is not None and plan.product.confidence < 0.5


def test_save_generated_character_to_personal_library(tmp_path: Path, fixture_page: Path) -> None:
    from vidai.agent.stages.character import load_library
    from vidai.cli import main as cli_main

    settings = _settings(tmp_path)
    plan = new_plan(settings, str(fixture_page), mode="auto", new_character=True)
    plan, _, _ = _run(plan, settings)
    assert plan.status == Status.READY, plan.error
    assert plan.character.source == "generated" and plan.characterBrief is not None
    assert plan.character.continuityAnchors.get("face") and plan.character.visualReferenceAssets
    assert cli_main(["save-character", plan.creativeId, "--data-dir", str(settings.data_dir)]) == 0
    personal = settings.data_dir / "personal_library.json"
    assert personal.is_file()
    library = load_library(None, personal)
    assert any(c.characterId == plan.character.characterId for c in library) and len(library) == 7
