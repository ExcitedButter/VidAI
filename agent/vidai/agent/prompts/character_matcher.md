# Module: Character Matcher (Stage G — Character Casting)

Casting decides "who says this", which is itself creative strategy. You receive the top library
candidates already scored by rules (category fit, audience fit, relatable / aspirational /
distinctive weights derived from the angle and category). For each candidate write one line
`whyThisPerson` the UI can show, and adjust `matchScore` (0-1) if the rule score misses something
obvious (e.g. the persona would not plausibly use this product). Respect the likeability
constraint: the target audience must want to keep watching this person; distinctive is fine,
grotesque or off-putting is not. Rank best first.

`productInteractionNeeds` says how this product must be handled on camera (worn, held, applied,
demoed); a candidate who could not plausibly do that loses points.

Input JSON: `{ "product", "audience", "sellingAngle", "weights": {relatable, aspirational, distinctive},
               "productInteractionNeeds": [], "candidates": [<Character with matchScore>] }`

Output schema:
{ "ranked": [ { "characterId": "", "matchScore": 0.0, "whyThisPerson": "" } ], "recommendedIndex": 0 }
