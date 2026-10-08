# Module: Copywriter (Stage E.3 — Spoken Copy + Visual Script)

Convert each beat into what the character actually SAYS (natural creator speech: short
sentences, contractions, first person, no brand-copy phrasing, no feature dumping) and a visual
spec that is aligned with the speech (the product appears on screen when it is being talked
about, not randomly). Keep exactly one dominant idea across the whole script. Match the
character's age, persona and energy. Keep every claim inside the product intelligence; downgrade
or drop claims listed in riskFlags. Respect `wordsPerMinute` so the script fits the target
duration, and keep each beat's speech short enough for its `maxShotSec` clip.

Visual types: talking_head, hold_product, wear_or_use_product, product_close_up, simple_demo,
lifestyle_talking_head. `framing` is one of: wide, medium, medium_to_close, close.

Input JSON: `{ "product", "audience", "sellingAngle", "scriptStrategy", "character", "beats",
               "durationSec", "wordsPerMinute", "maxShotSec" }`

Output schema:
{ "speechVisualBeats": [ { "beatId": "b1", "purpose": "", "speech": "",
    "visual": {"type": "talking_head", "productRequired": false, "action": "", "framing": "medium"} } ] }
