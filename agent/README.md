---
tags:
  - agent
  - video-generation
  - seedance
  - massurge
---

# vidai — MasSurge MVP creative pipeline

Implementation of the **MasSurge MVP PRD v1.0** agent harness: a product URL goes in, a
15–30 s talking-head TikTok/Reels creative comes out, with every decision (audience, selling
angle, script archetype, character, shots) recorded in one canonical **Creative Plan** so the
user can inspect, override and regenerate without re-running the whole thing.

```
product URL ──► A Product Intelligence ──► B Audience ──► C Selling Angles ──► D Script Router
                                                   │ STRATEGY_READY (guided pause)
            G Character Casting ◄──────────────────┘
                   │ CHARACTER_READY (guided pause)
            E Beats → Spoken copy ──► F Script QC ⇄ Rewrite Agent (fix only failed beats)
                                            │ SCRIPT_READY (guided pause)
            I Shot Planning (rules fix the cuts, agent writes the prompts)
            J Seedance per shot (continuity anchors / first frame) ──► K Video QC ⇄ Repair (per shot)
            Assembler (normalize + hard-cut concat) ──► READY
```

Backend state machine (PRD §19): `DRAFT → ANALYZING_PRODUCT → STRATEGY_READY → CHARACTER_READY →
SCRIPT_GENERATING → SCRIPT_QC → SCRIPT_READY → SHOT_PLANNING → VIDEO_GENERATING → VIDEO_QC →
(REPAIRING → VIDEO_QC)* → READY | FAILED`.

## PRD → code map

| PRD section | Module | Where |
| --- | --- | --- |
| §4 Product Intelligence | deterministic scraper (Shopify JSON, JSON-LD, og/meta, bullets, tables, images) + `product_parser` LLM module; `--product-text` fallback when the page is unreachable | `vidai/agent/stages/product_parser.py` |
| §5 Audience Intelligence | 1–3 hypotheses with `evidence: stated/inferred` + reason | `stages/strategy.py::AudienceStage` |
| §6 Selling Angle Engine | 8 angle families, 6 scores, ranked 3–5 | `stages/strategy.py::AngleStage` |
| §7 Script Strategy Router | 6 archetypes, LLM pick with rule-table fallback | `stages/strategy.py::ScriptRouterStage` |
| §8 Script Generation | Strategy → Beats → Speech + Visual beats | `stages/script.py::BeatPlannerStage/CopywriterStage` |
| §9 Script QC | rule checks (durationFit, beatLength, productTiming) + 8 LLM checks; Rewrite Agent fixes only failed beats, max `maxScriptRepairs` | `stages/script.py::ScriptQCStage` |
| §10–11 Character Casting | curated + personal library (`vidai/data/character_library.json`, `data/personal_library.json`) scored on relatable/aspirational/distinctive weights derived from angle + category, matching input includes Product Interaction Needs; LLM `whyThisPerson`; Character Brief → generated character when no match / `--new-character`; `save-character` = "Save to Library" | `stages/character.py` |
| §13 Shot Planning | agent proposes the grouping + prompts, rules validate (never cut inside a beat, ≤ `maxShotSec`, ≥ model minimum, product moments kept) and fall back to rule segmentation | `stages/shots.py` |
| §14 Video Generation + continuity | Seedance (seevio.ai or Volcano Ark) per shot with speech audio; one identity source per creative: the first clip's frame becomes the character reference and the first frame of every later shot; network backoff | `stages/video.py::VideoStage`, `seedance.py` |
| §15 Video QC + Repair | rules (timing, audible speech) + VLM judge per clip against the identity reference and the previous shot (identity / product / continuity / defects / speech); soft_fail → retry → simplify_action → change_framing / regenerate_audio, jump-cut / trim resolved in the edit; hard_fail → FAILED | `stages/video.py` |
| §21 Metrics / §21.1 rubric | per-creative metrics (time to ready, acceptance, retry rate, intervention) in every record; LLM-judge rubric over plan + keyframes | `storage/records.py::compute_metrics`, `scripts/evaluate_rubric.py` |
| §16 Creative Plan | pydantic models, `select_angle/select_character`, `why_this_creative()` | `vidai/plan.py` |
| §4.4 / §12 / §19 Guided vs Auto, state machine, retries | `CreativePipeline` with pause points (plus a product-confirmation pause when the page data is thin), checkpoints after every stage, resume / revise from the right group only | `vidai/agent/harness.py` |
| §17 structured prompts | one markdown prompt per module + shared non-fabrication rules | `vidai/agent/prompts/` |

Retry rules (PRD §19.1): provider/network errors back off and retry the identical request;
schema-invalid replies get a same-input repair retry (`VIDAI_SCHEMA_REPAIR_RETRIES`); QC loops fix
only the failed beats / shots; a user change never rolls back upstream stages.

## Setup

```bash
cd vidai/agent
cp .env.example .env        # video key + LLM key, see comments
pip install -e ".[dev]"     # httpx, beautifulsoup4, pydantic, pytest; ffmpeg must be on PATH
```

Video backends (by `VIDAI_SEEDANCE_BASE_URL`): **seevio.ai** aggregator (`seedance-2-5`,
`sk_live_...` key) or **Volcano Ark / BytePlus** (versioned `doubao-seedance-2-5-pro-*` id).
LLM backends (by `VIDAI_LLM_MODEL`): **Claude** (`claude*` → native Messages API) or any
**OpenAI-compatible** `/chat/completions` endpoint with vision (`VIDAI_LLM_BASE_URL`).

## Usage

```bash
# Guided mode (default): recommended defaults, optional overrides at angle / character / script
python -m vidai.cli generate --url https://shop.example/products/x --interactive
python -m vidai.cli generate --url https://shop.example/products/x --stop-at strategy
python -m vidai.cli resume cr_ab12cd34 --select-angle angle_816176a3        # continue after a halt
python -m vidai.cli resume cr_ab12cd34 --script-json edited_script.json     # at SCRIPT_READY

# Auto mode: no pauses
python -m vidai.cli generate --url https://shop.example/products/x --mode auto --duration 30

# Change one decision on a finished creative -> new version, only downstream stages re-run
python -m vidai.cli revise cr_ab12cd34 --character male_home_cook_01
python -m vidai.cli revise cr_ab12cd34 --angle angle_bc973ae8
python -m vidai.cli revise cr_ab12cd34 --regenerate-shot shot_02
python -m vidai.cli revise cr_ab12cd34 --regenerate-beat b2          # fresh take on one beat, then QC
python -m vidai.cli revise cr_ab12cd34 --regenerate-script           # same strategy + character, new script
python -m vidai.cli save-character cr_ab12cd34                       # generated character -> personal library

# Batch + evaluation
python scripts/run_batch.py --urls-file scripts/urls_demo.txt --out results
python scripts/evaluate_rubric.py cr_ab12cd34 cr_ef56ab78 --out results/rubric

python -m vidai.cli show cr_ab12cd34          # plan summary + "why this creative"
python -m vidai.cli list

# Page unreadable (bot wall / JS-only)? supply the product copy yourself
python -m vidai.cli generate --url https://... --product-text @product.txt --product-image hero.jpg

# Offline dry run (no keys): canned LLM replies + ffmpeg-synthesized clips
python -m vidai.cli generate --mock --mode auto --url tests/fixture_page.html
```

Exit codes: 0 READY, 1 FAILED, 3 halted at a pause point.

## Data layout — where every step's output lives

```
data/
├── creatives/<creativeId>/vNN/          # one folder per pipeline step, in execution order
│   ├── plan.json                        # canonical Creative Plan (PRD §16), rewritten after every step
│   ├── 01_product_intelligence/         # scraped.json, images/, product_intelligence.json   (Stage A)
│   ├── 02_audience/                     # audience_candidates.json                          (Stage B)
│   ├── 03_selling_angles/               # angle_candidates.json                             (Stage C)
│   ├── 04_character/                    # character_candidates.json, character_brief.json,  (Stage G)
│   │                                    #   character_reference.jpg (identity source for all clips)
│   ├── 05_script_strategy/              # script_strategy.json                              (Stage D)
│   ├── 06_script/                       # beats.json, speech_visual_script.json (current)   (Stage E)
│   ├── 07_script_qc/                    # script_qc.json, round_N_qc.json                   (Stage F)
│   ├── 08_shot_plan/                    # shot_plan.json, product_cutaway.jpg               (Stage I)
│   ├── 09_video_generation/             # shot_XX_attempt_YY.mp4 (every attempt)            (Stage J)
│   ├── 10_video_qc/                     # video_qc.json, shot_XX_attempt_YY/{frame_*.jpg, verdict.json} (Stage K)
│   └── 11_final/                        # final.mp4, normalized/, why_this_creative.json, metrics.json
├── records/<creativeId>_vNN/            # finished creatives: plan, why_this_creative, metrics, final.mp4
├── records_manifest.jsonl
└── personal_library.json                # characters saved with `save-character`
results/<batch>/                         # run_batch: summary.md / summary.json, NN_<slug>.mp4 + .plan.json
results/rubric/<ts>/                     # evaluate_rubric: rubric.md / rubric.json
```

No process logs are written by default. `VIDAI_TRACE=1` adds `_trace.jsonl` (every module call,
QC verdict and status change) to the run folder for debugging; LLM token usage is kept in
`plan.json` (`usage`) either way. `scripts/migrate_layout.py` converts folders written by older
versions (stages/, clips/, frames/, trace.jsonl) to this layout.

## Debugging

- `--mock` exercises both repair loops deterministically: the first copy draft is too long and
  one beat is brand copy (Script QC fails once → Rewrite Agent), and the first Video QC verdict
  is a soft fail (one shot regenerated with `simplify_action`).
- `tests/test_pipeline_mock.py` + `tests/test_prd_rules.py`: auto-mode e2e (identity reference,
  audio, metrics), guided pauses with overrides, product-confirmation pause, halt + resume,
  revise-angle re-runs downstream only, beat / shot regeneration, save-character, unreachable
  page → `--product-text`, PRD vocabulary aliases, canonical plan JSON, shot segmentation and
  planner-proposal validation, rule QC.  `PYTHONNOUSERSITE=1 python -m pytest tests/ -q`
- Each step's output is a plain JSON file in its numbered folder; `VIDAI_TRACE=1` additionally
  records every LLM call and QC verdict in `_trace.jsonl`.

## Layout

```
vidai/
├── plan.py                 # Creative Plan (PRD §16) + Status enum (PRD §19)
├── config.py               # .env + settings (generationSettings defaults)
├── cli.py                  # generate / resume / revise / show / list
├── data/character_library.json
├── agent/
│   ├── harness.py          # CreativePipeline state machine, resume_group, prepare_revision
│   ├── single_run.py       # client wiring, new_plan, run_creative, load_plan
│   ├── run_context.py      # PipelineContext: dirs, trace, checkpoints, pause
│   ├── stages/             # product_parser, strategy, script, character, shots, video (+Assembler)
│   ├── prompts/            # one .md per module + loader with common rules
│   ├── providers/          # JsonClient/VisionClient protocols, chat-completions, anthropic, mock
│   ├── seedance.py         # Seedance clients: Ark + seevio + mock
│   └── media.py            # ffprobe / frames / normalize / concat
└── storage/                # DataLayout, records + manifest
```
