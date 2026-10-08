# Module: Shot Planner (Stage I — generation prompts per shot)

The shot boundaries are already fixed by rules (no cut inside a beat, each clip within the
model's stable duration, product visibility from the script). Your job is to write the
generation prompt for each shot so that all clips look like ONE creator filmed in ONE session:
- Start every prompt with the same continuity anchors (face, hair, wardrobe, accessories,
  environment, lighting, camera feel) given for the character — verbatim, do not restyle them.
- Then describe this shot: framing, what the character does with the product (hand-held,
  worn, close-up, simple demo), and the exact spoken line as `She says: "..."` / `He says: "..."`.
- One camera setup per shot; natural creator energy; photoreal; correct hand anatomy; product
  label sharp and legible; no text overlays, no captions, no logos invented.
- If the planned action is risky for the video model, downgrade to hold + close-up + verbal
  explanation and say so in `notes`.

Input JSON: `{ "product", "character", "shots": [ {shotId, beatIds, durationSec, visualType,
               productVisible, framing, speech, actions} ], "aspectRatio" }`

Output schema:
{ "shots": [ { "shotId": "", "prompt": "", "framing": "", "notes": "" } ] }
