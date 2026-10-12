"""The repository must be usable straight from a clone (regressions that shipped once).

Each test here maps to a failure a new user hit on `git clone` + the README's own steps:
the curated character library missing from the repository, `cp .env.example .env` crashing
the config loader, and a missing video key surfacing only after the whole LLM pipeline ran.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vidai.agent.seedance import SeedanceNonRetryable, build_video_client, check_video_config
from vidai.agent.stages.character import DEFAULT_LIBRARY, load_library
from vidai.config import VidaiSettings, load_dotenv

AGENT_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- character library (PRD §11.2)
def test_curated_library_ships_with_the_repository() -> None:
    assert DEFAULT_LIBRARY.is_file(), f"{DEFAULT_LIBRARY} is missing from the clone"
    library = load_library(None)
    assert len(library) >= 3, "PRD §11.3 recommends 3 characters, so the library needs at least 3"
    for character in library:
        assert character.characterId and character.name
        assert character.continuityAnchors, f"{character.characterId} has no continuity anchors"


def test_library_is_tracked_by_git_not_swallowed_by_gitignore() -> None:
    """A bare `data/` rule in agent/.gitignore matched vidai/data/ too and hid this file."""
    result = subprocess.run(["git", "check-ignore", str(DEFAULT_LIBRARY)],
                            cwd=AGENT_ROOT, capture_output=True, text=True)
    assert result.returncode != 0, "the character library is being ignored by git"


# ------------------------------------------------------------------------- .env loading
@pytest.mark.parametrize("raw, expected", [
    ("15            # 15 | 30", "15"),          # exactly how .env.example writes it
    ("8  # stable clip length", "8"),
    ("guided", "guided"),
    ('"sk_live_a#b"', "sk_live_a#b"),           # '#' inside quotes is part of the value
    ("'x y'", "x y"),
    ("sk-abc#notacomment", "sk-abc#notacomment"),  # no whitespace -> not a comment
    ("https://api.seevio.ai", "https://api.seevio.ai"),
])
def test_dotenv_values_drop_inline_comments_but_keep_quoted_hashes(raw, expected) -> None:
    from vidai.config import _dotenv_value

    assert _dotenv_value(raw) == expected


def test_copying_env_example_produces_a_loadable_config(tmp_path, monkeypatch) -> None:
    """`cp .env.example .env` is the first instruction in the README; it must not crash."""
    env = tmp_path / ".env"
    env.write_text((AGENT_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    for key in [k for k in list(__import__("os").environ) if k.startswith("VIDAI_") or k == "ARK_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    load_dotenv(env)
    settings = VidaiSettings.from_env()          # used to raise ValueError on "15  # 15 | 30"
    assert settings.target_duration_s == 15
    assert settings.max_shot_s == 8.0


# ------------------------------------------------------------- video key preflight (PRD §19.1)
def test_missing_video_key_is_reported_before_anything_is_spent() -> None:
    reason = check_video_config(base_url="https://api.seevio.ai", api_key="", backend="auto")
    assert "ARK_API_KEY" in reason


@pytest.mark.parametrize("kwargs", [
    {"base_url": "https://api.seevio.ai", "api_key": "sk_live_x", "backend": "auto"},
    {"base_url": "http://127.0.0.1:8000", "api_key": "", "backend": "local"},   # local needs no key
    {"base_url": "https://api.seevio.ai", "api_key": "", "backend": "auto", "mock": True},
])
def test_usable_video_configurations_pass_preflight(kwargs) -> None:
    assert check_video_config(**kwargs) == ""


def test_empty_key_is_non_retryable_rather_than_a_network_blip(tmp_path) -> None:
    """The shot loop retries generic exceptions; httpx's ValueError used to land there.

    Construction must still succeed: clients are built before the pipeline starts, and a run
    that stops before video (`--stop-at script`) never needs the key.
    """
    import asyncio

    client = build_video_client(base_url="https://api.seevio.ai", api_key="", model="seedance-2-5")
    with pytest.raises(SeedanceNonRetryable):
        asyncio.run(client.generate_to_file(prompt="x", out_path=tmp_path / "clip.mp4"))
