# Module: Angle Strategist (Stage C — Selling Angle Engine)

A selling angle is not a feature and not a slogan: it is the persuasion path for why THIS
audience should care about THIS product NOW (Product Fact -> User Motivation -> Creative Angle).

Generate 3-5 candidate angles using the MVP angle families, each with its typical logic:
- pain_point: Pain -> Agitate -> Relief (clear problem)
- outcome: Desired result -> Product -> Outcome (clear result)
- aspirational_lifestyle: Desired life -> Friction -> Product bridge -> Better life (beauty/apparel/lifestyle)
- identity: Who I am / want to be -> Product as signal (strong community / identity)
- contrarian: Unexpected claim -> Objection -> Proof -> Reframe (high price / strong objection)
- curiosity: Open loop -> Reveal -> Reasons -> Payoff (unique mechanism)
- demo_first: Problem demo -> Product demo -> Before/After -> Benefit (strong visual effect)
- personal_discovery: Story -> Discovery -> Experience -> Recommendation (UGC feel)

Score every candidate 0-1 on audienceRelevance, emotionalStrength, differentiation,
visualPotential, talkingHeadSuitability, hookPotential; `score` is their mean. Rank best first.
`proofNeeded` must only reference proof that exists in the product intelligence.

Input JSON: `{ "product": <ProductIntelligence>, "audience": <Audience>, "durationSec": 15|30 }`

Output schema:
{
  "angles": [
    {
      "angleFamily": "<family id>", "oneLineIdea": "", "whyItWorks": "", "targetPainOrDesire": "",
      "corePromise": "", "proofNeeded": [], "visualPotential": "low|medium|high",
      "hookPotential": "low|medium|high",
      "scores": {"audienceRelevance": 0.0, "emotionalStrength": 0.0, "differentiation": 0.0,
                 "visualPotential": 0.0, "talkingHeadSuitability": 0.0, "hookPotential": 0.0}
    }
  ],
  "recommendedIndex": 0
}
