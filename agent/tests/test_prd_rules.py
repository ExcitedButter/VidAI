"""Unit tests for PRD-specific rules added in the strict-compliance pass."""

from __future__ import annotations

from vidai.agent.stages.shots import _ShotProposal, groups_from_proposal, segment_beats
from vidai.plan import Beat, CreativePlan, ScriptStrategy, SellingAngle, ShotQC, SpeechVisualBeat, VisualSpec


def _beat(beat_id: str, seconds: float, vtype: str = "talking_head", product: bool = False) -> SpeechVisualBeat:
    return SpeechVisualBeat(beatId=beat_id, speech="x", estimatedDurationSec=seconds,
                            visual=VisualSpec(type=vtype, productRequired=product))


def test_prd_vocabulary_is_accepted_by_the_schema() -> None:
    assert Beat(beatId="b1", purpose="p", message="m", visualIntent="hold_and_show_product").visualIntent == "hold_product"
    assert VisualSpec(type="product_demo").type == "simple_demo"
    assert VisualSpec(type="Product Close-Up").type == "product_close_up"
    assert SellingAngle(oneLineIdea="i", angleFamily="Pain-point").angleFamily == "pain_point"
    assert SellingAngle(oneLineIdea="i", angleFamily="Aspirational lifestyle").angleFamily == "aspirational_lifestyle"
    assert ScriptStrategy(archetype="PAS + Proof").archetype == "pas_proof"
    assert ScriptStrategy(archetype="Desired-Life Bridge").archetype == "desired_life_bridge"
    qc = ShotQC(status="failed", repairAction="retry", checks={"identity": {"status": "OK"}})
    assert qc.status == "soft_fail" and qc.repairAction == "retry_same_prompt" and qc.checks["identity"].status == "pass"
    assert ShotQC(status="pass", repairAction="not an action").repairAction is None


def test_canonical_plan_json_has_prd_top_level_fields() -> None:
    plan = CreativePlan()
    plan.angleCandidates = [SellingAngle(oneLineIdea="a"), SellingAngle(oneLineIdea="b")]
    plan.angleIndex = 1
    data = __import__("json").loads(plan.to_json())
    for key in ("creativeId", "mode", "product", "audience", "sellingAngle", "scriptStrategy", "character",
                "speechVisualBeats", "shotPlan", "generationSettings", "qc", "status"):
        assert key in data, key
    assert data["sellingAngle"]["oneLineIdea"] == "b"
    assert CreativePlan.from_json(plan.to_json()).sellingAngle.oneLineIdea == "b"


def test_segment_respects_model_minimum_clip_length() -> None:
    beats = [_beat("b1", 2.4), _beat("b2", 2.8), _beat("b3", 3.6, "hold_product", True),
             _beat("b4", 2.8, "product_close_up", True), _beat("b5", 3.2)]
    shots, warnings = segment_beats(beats, max_shot_s=8, min_shot_s=4, hard_cap_s=10)
    assert [s.beatIds for s in shots] == [["b1", "b2"], ["b3", "b4"], ["b5"]]
    assert all(s.durationSec >= 4 for s in shots) and shots[-1].speechSec == 3.2
    assert shots[1].productVisible and shots[1].visualType in ("hold_product", "product_close_up")
    assert not warnings


def test_planner_proposal_is_validated_against_hard_constraints() -> None:
    beats = [_beat("b1", 3), _beat("b2", 3), _beat("b3", 3, "hold_product", True)]
    ok, problem = groups_from_proposal([_ShotProposal(beatIds=["b1", "b2"]), _ShotProposal(beatIds=["b3"])], beats, 8)
    assert ok is not None and not problem and [[b.beatId for b in g] for g in ok] == [["b1", "b2"], ["b3"]]
    assert groups_from_proposal([_ShotProposal(beatIds=["b1", "b2", "b3"])], beats, 8)[0] is None       # 9 s > 8 s
    assert groups_from_proposal([_ShotProposal(beatIds=["b2", "b1"]), _ShotProposal(beatIds=["b3"])], beats, 8)[0] is None  # reordered
    assert groups_from_proposal([_ShotProposal(beatIds=["b1"]), _ShotProposal(beatIds=["b3"])], beats, 8)[0] is None        # b2 dropped
    assert groups_from_proposal([_ShotProposal(beatIds=["b1"]), _ShotProposal(beatIds=["b1", "b2", "b3"])], beats, 8)[0] is None


def test_product_visual_and_cutaway_prompts(tmp_path) -> None:
    import subprocess
    from vidai.agent.media import compose_on_canvas, ffprobe_metadata
    from vidai.agent.stages.shots import is_cutaway_shot, template_prompt
    from vidai.config import VidaiSettings
    from vidai.plan import ProductIntelligence, Shot

    hero = tmp_path / "hero.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=300x400:rate=1:duration=1",
                    "-frames:v", "1", str(hero)], check=True)
    product = ProductIntelligence(productName="Stagg EKG", category="kettle", heroImagePath=str(hero),
                                  visualDescription="a matte black gooseneck kettle with a wooden handle")
    settings = VidaiSettings()
    close = Shot(shotId="s1", visualType="product_close_up", productVisible=True, speech="pick a temperature")
    hold = Shot(shotId="s2", visualType="hold_product", productVisible=True, speech="hi")
    assert is_cutaway_shot(close, product, settings) and not is_cutaway_shot(hold, product, settings)
    settings.product_cutaway = False
    assert not is_cutaway_shot(close, product, settings)
    cut = template_prompt(close, None, product.productName, product.visualDescription, cutaway=True)
    assert "Voice-over" in cut and "gooseneck kettle" in cut and "pick a temperature" in cut
    held = template_prompt(hold, None, product.productName, product.visualDescription)
    assert "The product is a matte black gooseneck kettle" in held and "reference image" not in held
    canvas = compose_on_canvas(hero, tmp_path / "cut.jpg", (704, 1280))
    assert ffprobe_metadata(canvas)["width"] == 704 and ffprobe_metadata(canvas)["height"] == 1280
