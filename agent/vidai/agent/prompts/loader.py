"""Prompt loader: one markdown file per pipeline module (PRD §17.1)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent

COMMON_RULES = """
# Non-negotiable rules
- Input is structured JSON; output is ONE JSON object matching the schema below, nothing else.
- Never invent product facts. Every feature, claim, proof or price must be traceable to the
  provided product intelligence / page text. If something is unknown, leave it empty.
- Write for a 15-30 s TikTok / Reels talking-head product video: one dominant idea, natural
  spoken language, no feature dumping, no corporate ad voice.
- Sell a believable better life, not a feature list (feature -> functional benefit -> pain/desire
  -> aspirational identity -> better-life promise).
"""


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8").rstrip() + "\n" + COMMON_RULES
