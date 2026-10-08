# Module: Video QC (Stage K)

You see keyframes of ONE generated clip (first images), optionally followed by reference
keyframes of the previous clip / character reference. Judge, for this clip:
- identity: same face, hair, wardrobe, accessories as the reference (if any)
- product: correct product visible when required, no obvious distortion
- continuity: scene / lighting / camera consistent or an acceptable intentional jump cut
- visualDefects: hands, face, product geometry, severe flicker or morphing
- speech: lip / expression plausibly talking when speech is expected
Status: "pass"; "soft_fail" for fixable issues (retry, simplify action, change framing);
"hard_fail" for identity drift, wrong product, severe deformation or an empty/black clip.
Suggest ONE repairAction from: retry_same_prompt, simplify_action, change_framing,
replace_clip, trim_boundary, use_jump_cut, downgrade_visual_action.

Context JSON precedes the images. Output schema:
{ "status": "pass|soft_fail|hard_fail",
  "checks": { "identity": {"status": "", "reason": ""}, "product": {...}, "continuity": {...},
              "visualDefects": {...}, "speech": {...} },
  "repairAction": null | "<action>", "reason": "" }
