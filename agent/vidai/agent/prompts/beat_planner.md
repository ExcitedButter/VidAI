# Module: Beat Planner (Stage E.2 — Beat Outline)

Turn the strategy into semantic beats that follow the chosen archetype's structure. Each beat
has a purpose, the message it must land, a visual intent, whether the product must be on screen,
and an estimated duration. Beats must sum to roughly the target duration, and no single beat
may exceed `maxShotSec` (the video model's stable clip length) — split long ideas into two beats.

Visual intent types: talking_head, hold_product, wear_or_use_product, product_close_up,
simple_demo, lifestyle_talking_head. Product must be visible in at least one beat before or
while the core benefit is explained; do not make the character hold the product the whole time.
If a demo action is risky for a video model (complex hand manipulation), prefer hold_product +
product_close_up + verbal explanation.

Input JSON: `{ "product", "audience", "sellingAngle", "scriptStrategy", "character", "durationSec", "maxShotSec" }`

Output schema:
{ "beats": [ { "beatId": "b1", "purpose": "", "message": "", "visualIntent": "talking_head",
               "productRequired": false, "estimatedDurationSec": 3.0 } ] }
