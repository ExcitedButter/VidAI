"""Deterministic mock provider: canned per-module JSON so the whole PRD pipeline runs offline.

`complete_json` is keyed by `purpose` (= the module prompt name). The canned replies are shaped
to exercise the repair loops: the first copy draft is too long and one beat reads like brand
copy, so Script QC fails once and the Rewrite Agent fixes it; the first Video QC verdict is a
soft fail, so exactly one shot is regenerated with a simplified action.
"""

from __future__ import annotations

import json
from typing import Any

_FILLER = ["honestly", "like", "every single morning", "no exaggeration", "I mean it",
           "which is wild", "for real", "and I did not expect that", "trust me on this one",
           "and that is the whole point"]

# purpose, message, visual intent, product required, share of the target duration
_BEAT_PLAN = [
    ("hook", "Stop the scroll with the problem in one line", "talking_head", False, 0.15),
    ("pain", "Why the current way hurts", "talking_head", False, 0.20),
    ("solution", "The product and its one core benefit", "hold_product", True, 0.25),
    ("proof", "Concrete proof or lived experience", "product_close_up", True, 0.20),
    ("payoff_cta", "Better-life payoff and a soft CTA", "talking_head", False, 0.20),
]

_DRAFT_LINES = {  # first draft: long-winded; b2 is deliberately brand copy
    "b1": "Okay so if you love real espresso but you travel a lot, you already know the pain of hotel room coffee and sad airport shots.",
    "b2": "Experience premium barista-grade extraction with our innovative engineered pressure system designed for discerning coffee lovers everywhere.",
    "b3": "This is the {product}. You add grounds and hot water, press, and it pulls a proper shot anywhere, no power and no pods needed at all.",
    "b4": "Eighteen bar of pressure from a hand pump, three hundred forty grams, and it has well over a thousand five star reviews already.",
    "b5": "So now my mornings on the road actually feel like my mornings at home, and I am never going back to those little sachets.",
}
_REPAIR_LINES = {  # sized for the 15 s budget (~32 words); longer targets are padded
    "b1": "Hotel coffee was ruining my trips.",
    "b2": "Airport espresso? Burnt, every single time.",
    "b3": "Then I found this: grounds, water, press, espresso.",
    "b4": "Eighteen bar from a hand pump.",
    "b5": "Mornings on the road feel like home now.",
}
_ACTIONS = {
    "hold_product": "holds the {product} up at chest height and turns it toward the camera",
    "product_close_up": "brings the {product} close to the lens, label facing camera",
    "wear_or_use_product": "uses the {product} naturally while talking",
    "simple_demo": "shows one simple use of the {product}",
}
_FRAMING = {"product_close_up": "close", "hold_product": "medium", "talking_head": "medium_to_close"}


def _parse_payload(user_payload: str) -> dict[str, Any]:
    text = user_payload.split("\n\n<schema_repair>")[0]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _fit(text: str, n_words: int) -> str:
    """Keep the natural line when it is within 20 % of the word budget; otherwise trim or pad."""
    words = text.split()
    if 0.8 * n_words <= len(words) <= 1.2 * n_words:
        return text
    if len(words) > n_words:
        words = words[: max(n_words, 3)]
    index = 0
    while len(words) < n_words:
        words.extend(_FILLER[index % len(_FILLER)].split())
        index += 1
    words = words[: max(n_words, 3)]
    out = " ".join(words).rstrip(",;:")
    return out if out.endswith((".", "!", "?")) else out + "."


class MockProvider:
    """Implements JsonClient and VisionClient with canned, schema-valid replies."""

    model_id = "mock-planner"
    vlm_model_id = "mock-vlm"

    def __init__(self, *, fail_first_script_qc: bool = True, fail_first_video_qc: bool = True) -> None:
        self.calls: list[str] = []
        self.fail_first_script_qc = fail_first_script_qc
        self.fail_first_video_qc = fail_first_video_qc
        self._script_qc_calls = 0
        self._video_qc_calls = 0

    # ------------------------------------------------------------------ JsonClient
    async def complete_json(
        self, system_prompt: str, user_payload: str, *, purpose: str = "",
        image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(purpose)
        handler = getattr(self, f"_{purpose}", None)
        if handler is None:
            raise ValueError(f"mock provider has no canned reply for module {purpose!r}")
        return handler(_parse_payload(user_payload))

    async def vision_json(self, prompt: str, image_paths: list[str], purpose: str = "") -> dict[str, Any]:
        self.calls.append(purpose)
        if purpose == "video_qc":
            return self._judge_clip()
        return {"status": "pass", "note": f"mock vision reply for {purpose!r}"}

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------------ Stage A
    def _product_parser(self, payload: dict[str, Any]) -> dict[str, Any]:
        scraped = payload.get("scraped") or {}
        bullets = [b for b in scraped.get("bulletPoints", []) if b][:5]
        text = f"{scraped.get('pageText', '')} {scraped.get('description', '')}".lower()
        title = scraped.get("title") or ""
        return {
            "productName": title, "brandName": scraped.get("brand") or "",
            "category": "coffee gear" if "espresso" in text else "consumer product",
            "price": scraped.get("price"),
            "features": bullets or [s.strip() for s in text.split(".") if s.strip()][:4],
            "benefits": ["real espresso without a machine or power", "small enough to travel with"],
            "claims": ["barista-grade espresso anywhere"],
            "proofPoints": list(scraped.get("reviews", []))[:3],
            "useCases": ["travel", "camping", "office desk"],
            "brandTone": ["practical", "upbeat"],
            "positioning": scraped.get("description") or "",
            "constraints": ["no health claims"], "riskFlags": [],
            "visualDescription": "a compact matte-black electric gooseneck kettle with a wooden handle and lid knob, "
                                 "sitting on a round black base with a small display and dial; about two hands tall",
            "confidence": 0.85 if bullets else 0.45,
            "sourceNotes": list(scraped.get("notes", [])), "multipleProductsOnPage": False,
        }

    # ------------------------------------------------------------------ Stage B
    def _audience_analyst(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = (payload.get("product") or {}).get("productName") or "the product"
        return {
            "audiences": [
                {"segmentName": "Frequent travellers who care about coffee", "confidence": 0.8,
                 "demographics": {"age": "28-45", "gender": "mixed", "income": "middle to upper-middle", "location": "urban US / EU"},
                 "behaviors": ["travels monthly for work", "orders specialty coffee", "watches gear reviews"],
                 "painPoints": ["hotel and airport coffee is bad", "cannot pack a machine"],
                 "desires": ["a proper espresso ritual anywhere", "feel at home on the road"],
                 "purchaseTriggers": ["an upcoming trip", "gift season"],
                 "objections": ["is it a gimmick", "how much effort per shot"],
                 "currentLife": "lives out of a carry-on half the month",
                 "aspirationalIdentity": "the friend who always has the good coffee",
                 "aspirationalLifestyle": "slow, intentional mornings even on the road",
                 "languageStyle": ["casual", "first person", "specific"],
                 "evidence": "stated", "reason": f"The page positions {name} for travel and commuting and quotes commuter reviews."},
                {"segmentName": "Weekend campers and van-lifers", "confidence": 0.55,
                 "demographics": {"age": "25-40", "gender": "mixed", "income": "middle", "location": "suburban / outdoors"},
                 "behaviors": ["camps several times a year", "buys compact gear"],
                 "painPoints": ["instant coffee at the campsite"], "desires": ["a small luxury outdoors"],
                 "purchaseTriggers": ["season start"], "objections": ["one more thing to carry"],
                 "currentLife": "weekends outdoors", "aspirationalIdentity": "the well-equipped camper",
                 "aspirationalLifestyle": "sunrise espresso at the tent", "languageStyle": ["relaxed", "visual"],
                 "evidence": "inferred", "reason": "Camping is mentioned once in the use cases."},
            ],
            "recommendedIndex": 0,
        }

    # ------------------------------------------------------------------ Stage C
    def _angle_strategist(self, payload: dict[str, Any]) -> dict[str, Any]:
        def scores(*values: float) -> dict[str, float]:
            keys = ("audienceRelevance", "emotionalStrength", "differentiation",
                    "visualPotential", "talkingHeadSuitability", "hookPotential")
            return dict(zip(keys, values))
        return {
            "angles": [
                {"angleFamily": "pain_point", "oneLineIdea": "Hotel coffee is ruining your trips; fix it from your pocket",
                 "whyItWorks": "A pain every traveller recognises, resolved in one object",
                 "targetPainOrDesire": "bad coffee on the road", "corePromise": "real espresso anywhere, no power",
                 "proofNeeded": ["18 bar hand pump", "reviews"], "visualPotential": "high", "hookPotential": "high",
                 "scores": scores(0.9, 0.8, 0.7, 0.8, 0.9, 0.85)},
                {"angleFamily": "aspirational_lifestyle", "oneLineIdea": "Slow mornings do not have to stop when you travel",
                 "whyItWorks": "Sells the ritual, not the gadget", "targetPainOrDesire": "keep my ritual on the road",
                 "corePromise": "your morning ritual in a jacket pocket", "proofNeeded": ["340 g"],
                 "visualPotential": "high", "hookPotential": "medium", "scores": scores(0.8, 0.85, 0.6, 0.9, 0.8, 0.6)},
                {"angleFamily": "personal_discovery", "oneLineIdea": "The one thing I never travel without now",
                 "whyItWorks": "UGC discovery story builds trust", "targetPainOrDesire": "trust a new gadget",
                 "corePromise": "it genuinely replaced my machine on trips", "proofNeeded": ["reviews"],
                 "visualPotential": "medium", "hookPotential": "medium", "scores": scores(0.75, 0.7, 0.5, 0.6, 0.9, 0.6)},
            ],
            "recommendedIndex": 0,
        }

    # ------------------------------------------------------------------ Stage D
    def _script_router(self, payload: dict[str, Any]) -> dict[str, Any]:
        family = (payload.get("sellingAngle") or {}).get("angleFamily", "pain_point")
        archetype = {"aspirational_lifestyle": "desired_life_bridge", "identity": "desired_life_bridge",
                     "personal_discovery": "personal_discovery", "curiosity": "curiosity_reveal",
                     "contrarian": "contrarian_reframe", "demo_first": "demo_first"}.get(family, "pas_proof")
        return {"archetype": archetype,
                "reason": f"{family} angle with a clear problem and a visible object -> {archetype}",
                "coreMessage": "real espresso anywhere, without power or pods",
                "mainObjection": "is this just another gimmick",
                "emotionalDirection": "relief, then quiet pride",
                "requiredProof": ["18 bar hand pump", "1,300+ reviews"],
                "requiredProductMoments": ["hand-held reveal when the solution is introduced", "close-up during proof"]}

    # ------------------------------------------------------------------ Stage E
    def _beat_planner(self, payload: dict[str, Any]) -> dict[str, Any]:
        duration = float(payload.get("durationSec", 15))
        beats = []
        for index, (purpose, message, visual, product, share) in enumerate(_BEAT_PLAN, start=1):
            beats.append({"beatId": f"b{index}", "purpose": purpose, "message": message,
                          "visualIntent": visual, "productRequired": product,
                          "estimatedDurationSec": round(duration * share, 2)})
        return {"beats": beats}

    def _copy(self, payload: dict[str, Any], lines: dict[str, str], factor: float) -> dict[str, Any]:
        beats = payload.get("beats") or payload.get("speechVisualBeats") or []
        duration = float(payload.get("durationSec", 15))
        wpm = int(payload.get("wordsPerMinute", 150))
        count = max(len(beats), 1)
        budget = max((duration - 0.4 * count) * wpm / 60.0 * factor, 3.0 * count)
        product = (payload.get("product") or {}).get("productName") or "the product"
        shares = [float(b.get("estimatedDurationSec", 3.0)) for b in beats]
        total = sum(shares) or float(count)
        out = []
        for beat, share in zip(beats, shares):
            beat_id = beat["beatId"]
            line = (lines.get(beat_id) or f"{beat.get('purpose', '')}: {beat.get('message', '')}").replace("{product}", product)
            visual = beat.get("visual") or {}
            vtype = visual.get("type") or beat.get("visualIntent", "talking_head")
            required = bool(visual.get("productRequired", beat.get("productRequired", False)))
            out.append({
                "beatId": beat_id, "purpose": beat.get("purpose", ""),
                "speech": _fit(line, max(3, int(round(budget * share / total)))),
                "visual": {"type": vtype, "productRequired": required,
                           "action": _ACTIONS.get(vtype, "").replace("{product}", product),
                           "framing": _FRAMING.get(vtype, "medium")},
            })
        return {"speechVisualBeats": out}

    def _copywriter(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._copy(payload, _DRAFT_LINES, factor=1.6)      # too long on purpose

    def _script_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._copy(payload, _REPAIR_LINES, factor=1.0)     # fits the target

    # ------------------------------------------------------------------ Stage F
    def _script_qc(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._script_qc_calls += 1
        ids = [b["beatId"] for b in payload.get("speechVisualBeats", [])]
        checks = {name: {"status": "pass", "reason": "ok", "beatIds": []} for name in
                  ("hookStrength", "singleCoreAngle", "audienceFit", "characterFit",
                   "proofSpecificity", "aspirationalPayoff", "naturalSpeech", "claimSafety")}
        if self._script_qc_calls == 1 and self.fail_first_script_qc and ids:
            target = "b2" if "b2" in ids else ids[min(1, len(ids) - 1)]
            checks["naturalSpeech"] = {"status": "fail", "beatIds": [target],
                                       "reason": "reads like brand copy, not a creator talking"}
            return {"overall": "fail", "checks": checks,
                    "repairInstructions": [f"Rewrite beat {target} as natural first-person creator speech "
                                           "(short sentences, contractions); leave the other beats unchanged"]}
        return {"overall": "pass", "checks": checks, "repairInstructions": []}

    # ------------------------------------------------------------------ Stage G
    def _character_matcher(self, payload: dict[str, Any]) -> dict[str, Any]:
        segment = (payload.get("audience") or {}).get("segmentName", "this audience").lower()
        ranked = []
        for candidate in payload.get("candidates", []):
            first_name = (candidate.get("name") or "This creator").split(" - ")[0]
            ranked.append({"characterId": candidate["characterId"],
                           "matchScore": round(min(1.0, float(candidate.get("matchScore", 0.5)) + 0.05), 3),
                           "whyThisPerson": f"{first_name} plausibly belongs to '{segment}', so the "
                                            "recommendation reads as peer advice rather than an ad."})
        return {"ranked": ranked, "recommendedIndex": 0}

    def _character_brief(self, payload: dict[str, Any]) -> dict[str, Any]:
        weights = payload.get("weights") or {}
        return {
            "targetAudience": {"segment": (payload.get("audience") or {}).get("segmentName", "")},
            "characterGoal": "mixed",
            "relatableWeight": weights.get("relatable", 0.4), "aspirationalWeight": weights.get("aspirational", 0.4),
            "distinctiveWeight": weights.get("distinctive", 0.2),
            "ageRange": "30-38", "presentation": "masculine",
            "visualIdentity": {"face": "angular face, short stubble", "hair": "short dark curls", "skin": "medium", "build": "lean"},
            "style": {"wardrobe": "navy merino crewneck", "accessories": "steel field watch", "makeup": "none"},
            "personality": ["dry humour", "precise", "warm"],
            "lifestyleSignals": ["frequent flyer", "specialty coffee nerd"],
            "environment": "hotel room desk by the window, suitcase open behind",
            "distinctiveFeatures": ["grey streak at the temple"], "avoid": ["influencer gloss"],
            "categoryFit": ["coffee gear", "travel"], "productInteractionNeeds": ["hand-held demo"],
            "name": "Daniel - Carry-on Barista", "voice": "calm, dry, low American English",
            "continuityAnchors": {"face": "angular face, short stubble, grey streak at the temple",
                                  "hair": "short dark curls", "wardrobe": "navy merino crewneck",
                                  "accessories": "steel field watch", "environment": "hotel room desk by the window",
                                  "lighting": "soft window daylight camera-right", "voice": "calm, dry, low"},
        }

    # ------------------------------------------------------------------ Stage I
    def _shot_planner(self, payload: dict[str, Any]) -> dict[str, Any]:
        character = payload.get("character") or {}
        anchors = character.get("continuityAnchors") or {}
        anchor = ", ".join(anchors.get(k, "") for k in ("face", "hair", "wardrobe", "accessories", "environment", "lighting") if anchors.get(k))
        anchor = anchor or "a real creator filming a selfie-style phone video"
        product = (payload.get("product") or {}).get("productName") or "the product"
        shots = []
        for shot in payload.get("proposedShots") or payload.get("shots") or []:
            action = shot.get("actions") or _ACTIONS.get(shot.get("visualType", ""), "talks straight to the camera").replace("{product}", product)
            shots.append({"shotId": shot["shotId"], "beatIds": list(shot.get("beatIds", [])),
                          "visualType": shot.get("visualType", ""),
                          "prompt": f"{anchor}. {shot.get('framing', 'medium')} shot, handheld phone, single take. "
                                    f"The creator {action} and says: \"{shot.get('speech', '')}\". "
                                    "Photoreal, natural skin, correct hands, product label sharp, no captions.",
                          "framing": shot.get("framing", "medium"), "notes": ""})
        return {"shots": shots}

    # ------------------------------------------------------------------ Stage K (via complete_json)
    def _video_qc(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._judge_clip()

    def _judge_clip(self) -> dict[str, Any]:
        self._video_qc_calls += 1
        checks = {k: {"status": "pass", "reason": "ok"} for k in
                  ("identity", "product", "continuity", "visualDefects", "speech")}
        if self._video_qc_calls == 1 and self.fail_first_video_qc:
            checks["visualDefects"] = {"status": "fail", "reason": "fingers merge with the product on the hand-off"}
            return {"status": "soft_fail", "checks": checks, "repairAction": "simplify_action",
                    "reason": "hand / product geometry breaks during the hand-off"}
        return {"status": "pass", "checks": checks, "repairAction": None,
                "reason": "clip consistent with references"}

    # ------------------------------------------------------------------ evaluation rubric (PRD §21.1)
    def _rubric_eval(self, payload: dict[str, Any]) -> dict[str, Any]:
        dims = ("audienceFit", "sellingAngleStrength", "characterFit", "naturalness",
                "productIntegration", "continuity", "overallPublishability")
        return {"scores": {d: {"score": 4, "reason": "mock judge", "planLayer": "script"} for d in dims},
                "lowestDimension": "continuity", "summary": "mock rubric evaluation"}
