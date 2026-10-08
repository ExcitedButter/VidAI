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
| §10–11 Character Casting | curated library (`vidai/data/character_library.json`) scored on relatable/aspirational/distinctive weights derived from angle + category; LLM `whyThisPerson`; Character Brief → generated character when no match / `--new-character` | `stages/character.py` |
| §13 Shot Planning | hard constraints: never cut inside a beat, ≤ `maxShotSec`, cut on visual-type / product change; LLM writes per-shot prompts starting with the continuity anchors | `stages/shots.py` |
| §14 Video Generation | Seedance (seevio.ai or Volcano Ark) per shot, first frame = character reference when available, network backoff | `stages/video.py::VideoStage`, `seedance.py` |
| §15 Video QC + Repair | ffprobe timing + VLM judge per clip (identity / product / continuity / defects / speech); soft_fail → retry → simplify_action → change_framing; hard_fail → FAILED | `stages/video.py` |
| §16 Creative Plan | pydantic models, `select_angle/select_character`, `why_this_creative()` | `vidai/plan.py` |
| §12 / §19 Guided vs Auto, state machine, retries | `CreativePipeline` with pause points, checkpoints after every stage, resume / revise from the right group only | `vidai/agent/harness.py` |
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

python -m vidai.cli show cr_ab12cd34          # plan summary + "why this creative"
python -m vidai.cli list

# Page unreadable (bot wall / JS-only)? supply the product copy yourself
python -m vidai.cli generate --url https://... --product-text @product.txt --product-image hero.jpg

# Offline dry run (no keys): canned LLM replies + ffmpeg-synthesized clips
python -m vidai.cli generate --mock --mode auto --url tests/fixture_page.html
```

Exit codes: 0 READY, 1 FAILED, 3 halted at a pause point.

## Data layout

```
data/
├── creatives/<creativeId>/vNN/      # one dir per plan version
│   ├── plan.json                    # canonical Creative Plan (latest)
│   ├── stages/NN_<stage>.json       # checkpoint after every stage (inspect / resume any step)
│   ├── product/{scraped.json,images/}
│   ├── clips/shot_XX_aYY.mp4        # every generation attempt, *_norm.mp4 normalized
│   ├── frames/                      # QC keyframes
│   ├── final.mp4
│   └── trace.jsonl                  # every module call, QC verdict, status change
├── records/<creativeId>_vNN/        # READY / FAILED: plan, why_this_creative.json, final.mp4, trace
└── records_manifest.jsonl
```

## Debugging

- `--mock` exercises both repair loops deterministically: the first copy draft is too long and
  one beat is brand copy (Script QC fails once → Rewrite Agent), and the first Video QC verdict
  is a soft fail (one shot regenerated with `simplify_action`).
- `tests/test_pipeline_mock.py`: auto-mode e2e, guided pauses with overrides, halt + resume,
  revise-angle re-runs downstream only, single-shot regeneration, unreachable page →
  `--product-text`, and unit tests for shot segmentation / rule QC.
  `PYTHONNOUSERSITE=1 python -m pytest tests/ -q`
- Every LLM call (payload, reply, schema-repair attempts) is in `trace.jsonl`; every stage output
  is a checkpoint under `stages/`.

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
