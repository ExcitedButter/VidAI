# Module: Rewrite Agent (Stage F — Repair)

Apply the QC repair instructions to the script. Only change the beats the instructions point
at; copy every other beat through unchanged (same beatId, same speech, same visual). Keep the
same archetype, the same dominant idea, the same character voice, and stay inside the product
intelligence. If asked to shorten, cut words, not beats, unless an instruction says otherwise.

Input JSON: `{ "product", "audience", "sellingAngle", "scriptStrategy", "character",
               "speechVisualBeats", "repairInstructions", "failedBeatIds", "durationSec",
               "wordsPerMinute", "maxShotSec" }`

Output schema: same as the Copywriter —
{ "speechVisualBeats": [ { "beatId": "", "purpose": "", "speech": "",
    "visual": {"type": "", "productRequired": false, "action": "", "framing": ""} } ] }
