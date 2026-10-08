# Module: Character Brief (Stage G — Generate New Character)

Write a structured Character Brief for creating a new on-camera creator for this product and
audience. The brief is the only canonical input for character generation — no free-form prompts.
Lock continuity anchors (face, hair, wardrobe, accessories, environment, lighting, voice) so every
clip can reuse them. "Aspirational but attainable": the audience must think "I could be her /
him", not a runway model. Distinctive features must create pattern interruption without being
grotesque. Honor the weights.

Input JSON: `{ "product", "audience", "sellingAngle", "weights", "productInteractionNeeds": [], "avoid": [] }`

Output schema:
{
  "targetAudience": {}, "characterGoal": "aspirational|relatable|distinctive|mixed",
  "relatableWeight": 0.35, "aspirationalWeight": 0.5, "distinctiveWeight": 0.15,
  "ageRange": "", "presentation": "", "visualIdentity": {"face": "", "hair": "", "skin": "", "build": ""},
  "style": {"wardrobe": "", "accessories": "", "makeup": ""}, "personality": [],
  "lifestyleSignals": [], "environment": "", "distinctiveFeatures": [], "avoid": [],
  "categoryFit": [], "productInteractionNeeds": [],
  "name": "", "voice": "", "continuityAnchors": {"face": "", "hair": "", "wardrobe": "", "accessories": "", "environment": "", "lighting": "", "voice": ""}
}
