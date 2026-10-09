"""Canonical Creative Plan — the single source of truth (MasSurge MVP PRD §16).

Every stage reads from and writes into one `CreativePlan`. The frontend / CLI may edit it
(angle, character, script); downstream stages only ever consume the plan.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


class Status(str, Enum):
    """Backend state machine (PRD §19)."""

    DRAFT = "DRAFT"
    ANALYZING_PRODUCT = "ANALYZING_PRODUCT"
    STRATEGY_READY = "STRATEGY_READY"
    CHARACTER_READY = "CHARACTER_READY"
    SCRIPT_GENERATING = "SCRIPT_GENERATING"
    SCRIPT_QC = "SCRIPT_QC"
    SCRIPT_READY = "SCRIPT_READY"
    SHOT_PLANNING = "SHOT_PLANNING"
    VIDEO_GENERATING = "VIDEO_GENERATING"
    VIDEO_QC = "VIDEO_QC"
    REPAIRING = "REPAIRING"
    READY = "READY"
    FAILED = "FAILED"


Mode = Literal["guided", "auto"]
AngleFamily = Literal[
    "pain_point", "outcome", "aspirational_lifestyle", "identity",
    "contrarian", "curiosity", "demo_first", "personal_discovery",
]
Archetype = Literal[
    "pas_proof", "personal_discovery", "desired_life_bridge",
    "contrarian_reframe", "demo_first", "curiosity_reveal",
]
VisualType = Literal[
    "talking_head", "hold_product", "wear_or_use_product",
    "product_close_up", "simple_demo", "lifestyle_talking_head",
]
CheckStatus = Literal["pass", "fail", "warn"]
RepairAction = Literal[
    "retry_same_prompt", "simplify_action", "change_framing", "replace_clip",
    "trim_boundary", "use_jump_cut", "regenerate_audio", "downgrade_visual_action",
]

ANGLE_FAMILIES: tuple[str, ...] = AngleFamily.__args__  # type: ignore[attr-defined]
ARCHETYPES: tuple[str, ...] = Archetype.__args__  # type: ignore[attr-defined]
VISUAL_TYPES: tuple[str, ...] = VisualType.__args__  # type: ignore[attr-defined]

# The PRD uses informal spellings in its examples (hold_and_show_product, product_demo, "PAS + Proof");
# normalize them so LLM output written in PRD vocabulary never fails schema validation.
_VISUAL_ALIASES = {
    "hold_and_show_product": "hold_product", "show_product": "hold_product", "product_reveal": "hold_product",
    "hold": "hold_product", "holding_product": "hold_product",
    "product_demo": "simple_demo", "demo": "simple_demo", "product_interaction": "simple_demo",
    "wear_product": "wear_or_use_product", "use_product": "wear_or_use_product",
    "wearing_product": "wear_or_use_product", "using_product": "wear_or_use_product", "wear": "wear_or_use_product",
    "close_up": "product_close_up", "closeup": "product_close_up", "product_closeup": "product_close_up",
    "lifestyle": "lifestyle_talking_head", "talking_head_with_product": "lifestyle_talking_head",
    "talking_head_product_visible": "lifestyle_talking_head",
}
_ANGLE_ALIASES = {"pain": "pain_point", "painpoint": "pain_point", "pain_point_relief": "pain_point",
                  "lifestyle": "aspirational_lifestyle", "aspirational": "aspirational_lifestyle",
                  "demo": "demo_first", "discovery": "personal_discovery", "ugc": "personal_discovery"}
_ARCHETYPE_ALIASES = {"pas": "pas_proof", "pas_proof_cta": "pas_proof", "pas_and_proof": "pas_proof",
                      "pasproof": "pas_proof", "desired_life": "desired_life_bridge", "contrarian": "contrarian_reframe",
                      "demo": "demo_first", "curiosity": "curiosity_reveal", "discovery": "personal_discovery"}
_STATUS_ALIASES = {"ok": "pass", "passed": "pass", "passing": "pass", "true": "pass", "failed": "fail",
                   "failing": "fail", "error": "fail", "false": "fail", "warning": "warn", "caution": "warn"}
_SHOT_STATUS_ALIASES = {"ok": "pass", "passed": "pass", "fail": "soft_fail", "failed": "soft_fail", "soft": "soft_fail",
                        "softfail": "soft_fail", "warn": "soft_fail", "hard": "hard_fail", "hardfail": "hard_fail"}
_REPAIR_ALIASES = {"retry": "retry_same_prompt", "retry_same": "retry_same_prompt", "regenerate": "retry_same_prompt",
                   "simplify": "simplify_action", "simplify_motion": "simplify_action", "reframe": "change_framing",
                   "framing": "change_framing", "replace": "replace_clip", "trim": "trim_boundary",
                   "jump_cut": "use_jump_cut", "jumpcut": "use_jump_cut", "audio": "regenerate_audio",
                   "downgrade": "downgrade_visual_action", "downgrade_action": "downgrade_visual_action"}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def normalize_visual_type(value: Any) -> Any:
    """Map any reasonable spelling onto the 6 MVP visual beat types (PRD §12.2)."""
    if not isinstance(value, str):
        return value
    key = _key(value)
    if key in VISUAL_TYPES:
        return key
    if key in _VISUAL_ALIASES:
        return _VISUAL_ALIASES[key]
    if "close" in key:
        return "product_close_up"
    if "demo" in key:
        return "simple_demo"
    if "wear" in key or "use" in key or "apply" in key:
        return "wear_or_use_product"
    if "hold" in key or "show" in key or "reveal" in key:
        return "hold_product"
    if "lifestyle" in key:
        return "lifestyle_talking_head"
    return "talking_head"


def normalize_enum(value: Any, allowed: tuple[str, ...], aliases: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    key = _key(value)
    return key if key in allowed else aliases.get(key, value)


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


# ----------------------------------------------------------------------------- Stage A
class ProductIntelligence(PlanModel):
    """PRD §4.3 — facts only; nothing may be invented by the model."""

    productName: str = ""
    brandName: str = ""
    category: str = ""
    price: Optional[str] = None
    features: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    proofPoints: list[str] = Field(default_factory=list)
    useCases: list[str] = Field(default_factory=list)
    visualAssets: list[str] = Field(default_factory=list)
    brandTone: list[str] = Field(default_factory=list)
    positioning: str = ""
    constraints: list[str] = Field(default_factory=list)
    riskFlags: list[str] = Field(default_factory=list)
    visualDescription: str = ""   # what the product looks like, for a text-only video generator
    # bookkeeping (PRD §4.4)
    confidence: float = 0.0
    sourceUrl: str = ""
    sourceNotes: list[str] = Field(default_factory=list)
    multipleProductsOnPage: bool = False
    heroImagePath: Optional[str] = None

    @field_validator("price", mode="before")
    @classmethod
    def _price_as_text(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return value if isinstance(value, str) else str(value)


# ----------------------------------------------------------------------------- Stage B
class Audience(PlanModel):
    """PRD §5.3 (+ evidence / reason required by §5.4)."""

    segmentName: str
    confidence: float = 0.5
    demographics: dict[str, str] = Field(default_factory=dict)
    behaviors: list[str] = Field(default_factory=list)
    painPoints: list[str] = Field(default_factory=list)
    desires: list[str] = Field(default_factory=list)
    purchaseTriggers: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    currentLife: str = ""
    aspirationalIdentity: str = ""
    aspirationalLifestyle: str = ""
    languageStyle: list[str] = Field(default_factory=list)
    evidence: Literal["stated", "inferred"] = "inferred"
    reason: str = ""


# ----------------------------------------------------------------------------- Stage C
class SellingAngle(PlanModel):
    """PRD §6.4."""

    angleId: str = Field(default_factory=lambda: new_id("angle"))
    angleFamily: AngleFamily = "outcome"
    oneLineIdea: str
    whyItWorks: str = ""
    targetPainOrDesire: str = ""
    corePromise: str = ""
    proofNeeded: list[str] = Field(default_factory=list)
    visualPotential: Literal["low", "medium", "high"] = "medium"
    hookPotential: Literal["low", "medium", "high"] = "medium"
    scores: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0

    @field_validator("angleFamily", mode="before")
    @classmethod
    def _norm_family(cls, value: Any) -> Any:
        return normalize_enum(value, ANGLE_FAMILIES, _ANGLE_ALIASES)


# ----------------------------------------------------------------------------- Stage D / E
class ScriptStrategy(PlanModel):
    """PRD §7.3 router output + §8.1 strategy step."""

    archetype: Archetype = "pas_proof"
    reason: str = ""
    coreMessage: str = ""
    mainObjection: str = ""
    emotionalDirection: str = ""
    requiredProof: list[str] = Field(default_factory=list)
    requiredProductMoments: list[str] = Field(default_factory=list)

    @field_validator("archetype", mode="before")
    @classmethod
    def _norm_archetype(cls, value: Any) -> Any:
        return normalize_enum(value, ARCHETYPES, _ARCHETYPE_ALIASES)


class Beat(PlanModel):
    """PRD §8.2 semantic beat."""

    beatId: str
    purpose: str
    message: str
    visualIntent: VisualType = "talking_head"
    productRequired: bool = False
    estimatedDurationSec: float = 3.0

    @field_validator("visualIntent", mode="before")
    @classmethod
    def _norm_intent(cls, value: Any) -> Any:
        return normalize_visual_type(value)


class VisualSpec(PlanModel):
    type: VisualType = "talking_head"
    productRequired: bool = False
    action: str = ""
    framing: str = "medium"

    @field_validator("type", mode="before")
    @classmethod
    def _norm_type(cls, value: Any) -> Any:
        return normalize_visual_type(value)


class SpeechVisualBeat(PlanModel):
    """PRD §8.4 — canonical script form: Speech + Visual."""

    beatId: str
    purpose: str = ""
    speech: str
    visual: VisualSpec = Field(default_factory=VisualSpec)
    estimatedDurationSec: float = 3.0


# ----------------------------------------------------------------------------- Stage F
class QCCheck(PlanModel):
    status: CheckStatus = "pass"
    reason: str = ""
    beatIds: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def _norm_status(cls, value: Any) -> Any:
        return normalize_enum(value, ("pass", "fail", "warn"), _STATUS_ALIASES)


class ScriptQC(PlanModel):
    """PRD §9.3."""

    overall: CheckStatus = "pass"
    checks: dict[str, QCCheck] = Field(default_factory=dict)
    repairInstructions: list[str] = Field(default_factory=list)
    estimatedDurationSec: float = 0.0
    repairRounds: int = 0
    warnings: list[str] = Field(default_factory=list)

    @field_validator("overall", mode="before")
    @classmethod
    def _norm_overall(cls, value: Any) -> Any:
        return normalize_enum(value, ("pass", "fail", "warn"), _STATUS_ALIASES)


# ----------------------------------------------------------------------------- Stage G
class CharacterScores(PlanModel):
    aspiration: float = 0.5
    distinctiveness: float = 0.5
    trust: float = 0.5
    relatability: float = 0.5


class Character(PlanModel):
    """PRD §11.2 library schema + continuity anchors (§14.3)."""

    characterId: str
    name: str
    source: Literal["curated", "generated"] = "curated"
    visualReferenceAssets: list[str] = Field(default_factory=list)
    ageRange: str = ""
    presentation: str = ""
    personality: list[str] = Field(default_factory=list)
    lifestyle: str = ""
    environment: str = ""
    wardrobe: str = ""
    voice: str = ""
    bestForCategories: list[str] = Field(default_factory=list)
    bestForAudiences: list[str] = Field(default_factory=list)
    scores: CharacterScores = Field(default_factory=CharacterScores)
    continuityAnchors: dict[str, str] = Field(default_factory=dict)
    # matching output (§11.3)
    matchScore: float = 0.0
    whyThisPerson: str = ""


class CharacterBrief(PlanModel):
    """PRD §11.1 — the only canonical input for Generate New Character."""

    targetAudience: dict[str, Any] = Field(default_factory=dict)
    characterGoal: Literal["aspirational", "relatable", "distinctive", "mixed"] = "mixed"
    relatableWeight: float = 0.35
    aspirationalWeight: float = 0.50
    distinctiveWeight: float = 0.15
    ageRange: str = ""
    presentation: str = ""
    visualIdentity: dict[str, str] = Field(default_factory=dict)
    style: dict[str, str] = Field(default_factory=dict)
    personality: list[str] = Field(default_factory=list)
    lifestyleSignals: list[str] = Field(default_factory=list)
    environment: str = ""
    distinctiveFeatures: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    categoryFit: list[str] = Field(default_factory=list)
    productInteractionNeeds: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------- Stage I / J / K
class ShotQC(PlanModel):
    status: Literal["pass", "soft_fail", "hard_fail"] = "pass"
    checks: dict[str, QCCheck] = Field(default_factory=dict)
    repairAction: Optional[RepairAction] = None
    reason: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _norm_shot_status(cls, value: Any) -> Any:
        return normalize_enum(value, ("pass", "soft_fail", "hard_fail"), _SHOT_STATUS_ALIASES)

    @field_validator("repairAction", mode="before")
    @classmethod
    def _norm_repair(cls, value: Any) -> Any:
        if value in (None, "", "none", "null"):
            return None
        normalized = normalize_enum(value, RepairAction.__args__, _REPAIR_ALIASES)  # type: ignore[attr-defined]
        return normalized if normalized in RepairAction.__args__ else None  # type: ignore[attr-defined]


class Shot(PlanModel):
    """PRD §13.3 shot plan entry + generation / QC state."""

    shotId: str
    title: str = ""
    beatIds: list[str] = Field(default_factory=list)
    durationSec: float = 4.0          # clip length requested from the video model
    speechSec: float = 0.0            # planned spoken duration of the beats in this shot
    visualType: VisualType = "talking_head"
    productVisible: bool = False
    framing: str = "medium"
    speech: str = ""
    prompt: str = ""
    continuityAnchors: dict[str, str] = Field(default_factory=dict)
    status: Literal["planned", "generated", "passed", "failed"] = "planned"
    clipPath: Optional[str] = None
    attempts: int = 0
    qc: Optional[ShotQC] = None
    repairHistory: list[str] = Field(default_factory=list)

    @field_validator("visualType", mode="before")
    @classmethod
    def _norm_shot_type(cls, value: Any) -> Any:
        return normalize_visual_type(value)


class VideoQC(PlanModel):
    overall: Literal["pass", "fail", "warn"] = "pass"
    shots: dict[str, ShotQC] = Field(default_factory=dict)
    repairRounds: int = 0
    warnings: list[str] = Field(default_factory=list)

    @field_validator("overall", mode="before")
    @classmethod
    def _norm_video_overall(cls, value: Any) -> Any:
        return normalize_enum(value, ("pass", "fail", "warn"), _STATUS_ALIASES)


class GenerationSettings(PlanModel):
    targetDurationSec: int = 15
    aspectRatio: str = "9:16"
    resolution: str = "720p"
    videoModel: str = ""
    maxShotSec: float = 8.0
    minShotSec: float = 2.0
    wordsPerMinute: int = 150
    maxScriptRepairs: int = 2
    maxVideoRepairs: int = 2
    allowJumpCuts: bool = True


class StageEvent(PlanModel):
    stage: str
    status: str
    startedAt: str
    finishedAt: str = ""
    ok: bool = True
    note: str = ""


# ----------------------------------------------------------------------------- the plan
class CreativePlan(PlanModel):
    """PRD §16 canonical Creative Plan."""

    creativeId: str = Field(default_factory=lambda: new_id("cr"))
    version: int = 1
    mode: Mode = "guided"
    createdAt: str = Field(default_factory=utc_now_iso)
    productUrl: str = ""
    productTextFallback: str = ""
    product: Optional[ProductIntelligence] = None
    audienceCandidates: list[Audience] = Field(default_factory=list)
    audienceIndex: Optional[int] = None
    angleCandidates: list[SellingAngle] = Field(default_factory=list)
    angleIndex: Optional[int] = None
    scriptStrategy: Optional[ScriptStrategy] = None
    characterCandidates: list[Character] = Field(default_factory=list)
    characterIndex: Optional[int] = None
    characterBrief: Optional[CharacterBrief] = None
    beats: list[Beat] = Field(default_factory=list)
    speechVisualBeats: list[SpeechVisualBeat] = Field(default_factory=list)
    shotPlan: list[Shot] = Field(default_factory=list)
    generationSettings: GenerationSettings = Field(default_factory=GenerationSettings)
    qc: dict[str, Any] = Field(default_factory=lambda: {"script": None, "video": None})
    status: Status = Status.DRAFT
    history: list[StageEvent] = Field(default_factory=list)
    userOverrides: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=lambda: {"llmCalls": 0, "promptTokens": 0, "completionTokens": 0})
    finalVideo: Optional[str] = None
    error: Optional[str] = None

    # -------------------------------------------------------------- selections
    @computed_field  # type: ignore[misc]
    @property
    def audience(self) -> Optional[Audience]:
        if self.audienceIndex is None or not self.audienceCandidates:
            return None
        return self.audienceCandidates[self.audienceIndex]

    @computed_field  # type: ignore[misc]
    @property
    def sellingAngle(self) -> Optional[SellingAngle]:
        if self.angleIndex is None or not self.angleCandidates:
            return None
        return self.angleCandidates[self.angleIndex]

    @computed_field  # type: ignore[misc]
    @property
    def character(self) -> Optional[Character]:
        if self.characterIndex is None or not self.characterCandidates:
            return None
        return self.characterCandidates[self.characterIndex]

    @property
    def script_qc(self) -> Optional[ScriptQC]:
        value = self.qc.get("script")
        return ScriptQC.model_validate(value) if isinstance(value, dict) else value

    @property
    def video_qc(self) -> Optional[VideoQC]:
        value = self.qc.get("video")
        return VideoQC.model_validate(value) if isinstance(value, dict) else value

    def select_angle(self, angle_id: str, *, by_user: bool = False) -> None:
        for index, angle in enumerate(self.angleCandidates):
            if angle.angleId == angle_id:
                self.angleIndex = index
                if by_user:
                    self.userOverrides["angle"] = angle_id
                return
        raise KeyError(f"unknown angleId {angle_id!r}; candidates: "
                       f"{[a.angleId for a in self.angleCandidates]}")

    def select_character(self, character_id: str, *, by_user: bool = False) -> None:
        for index, character in enumerate(self.characterCandidates):
            if character.characterId == character_id:
                self.characterIndex = index
                if by_user:
                    self.userOverrides["character"] = character_id
                return
        raise KeyError(f"unknown characterId {character_id!r}; candidates: "
                       f"{[c.characterId for c in self.characterCandidates]}")

    # -------------------------------------------------------------- summaries
    def why_this_creative(self) -> dict[str, Any]:
        """PRD §3.3 — the explanation shown on the result page."""
        audience, angle, character = self.audience, self.sellingAngle, self.character
        return {
            "product": self.product.productName if self.product else None,
            "audience": {"segment": audience.segmentName, "evidence": audience.evidence,
                         "reason": audience.reason} if audience else None,
            "sellingAngle": {"family": angle.angleFamily, "idea": angle.oneLineIdea,
                             "whyItWorks": angle.whyItWorks} if angle else None,
            "scriptArchetype": {"archetype": self.scriptStrategy.archetype,
                                "reason": self.scriptStrategy.reason} if self.scriptStrategy else None,
            "character": {"name": character.name, "source": character.source,
                          "whyThisPerson": character.whyThisPerson} if character else None,
            "userOverrides": dict(self.userOverrides),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=False)

    @classmethod
    def from_json(cls, text: str) -> "CreativePlan":
        return cls.model_validate_json(text)
