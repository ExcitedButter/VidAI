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
