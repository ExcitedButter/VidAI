# Module: Script Strategy Router (Stage D) + Strategy step (Stage E.1)

Decide which persuasion STRUCTURE fits, before any copy is written. You are not the copywriter.

MVP archetypes:
- pas_proof: Hook -> Pain -> Agitate -> Solution -> Proof -> CTA (clear pain, clear solution)
- personal_discovery: Hook -> Story -> Discovery -> Experience -> Recommendation (UGC / creator)
- desired_life_bridge: Desired life -> Current friction -> Product bridge -> Better life (beauty, activewear, lifestyle)
- contrarian_reframe: Contrarian hook -> Objection -> Demo/Proof -> Reframe -> CTA (price / trust / misconceptions)
- demo_first: Problem demo -> Product demo -> Result -> Benefit (strong visual demonstration)
- curiosity_reveal: Curiosity hook -> Reveal -> Reasons -> Payoff (unique mechanism / comparison)

Also fix the strategy the beats must follow: the single core message, the main objection to
handle, the emotional direction, the proof that must appear (only proof that exists), and how /
when the product must appear on screen (it must appear at least when the core benefit is
explained; natural hand-held or worn/used where possible).

Input JSON: `{ "product", "audience", "sellingAngle", "character" (may be null), "durationSec" }`

Output schema:
{
  "archetype": "<archetype id>", "reason": "", "coreMessage": "", "mainObjection": "",
  "emotionalDirection": "", "requiredProof": [], "requiredProductMoments": []
}
