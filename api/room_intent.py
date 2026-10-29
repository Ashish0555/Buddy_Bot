"""Natural-language Smash Karts room intent extraction and resolution."""

from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    from .room_creator import (
        ARENA_IDS,
        DEFAULT_WEAPONS,
        VALID_MAX_PLAYERS,
        VALID_SCORE_TARGETS,
        VALID_TIMERS,
        WEAPON_BITS,
        create_room_url,
        normalize_arena,
    )
except ImportError:
    from room_creator import (
        ARENA_IDS,
        DEFAULT_WEAPONS,
        VALID_MAX_PLAYERS,
        VALID_SCORE_TARGETS,
        VALID_TIMERS,
        WEAPON_BITS,
        create_room_url,
        normalize_arena,
    )


class RoomIntent(BaseModel):
    """Gemini's explicit room-request extraction."""

    model_config = ConfigDict(extra="forbid", strict=True)

    is_room_request: bool
    map: str | None = None
    mode: Literal["ffa", "ctf"] | None = None
    players: int | None = None
    round_format: Literal["timer", "score"] | None = None
    value: int | None = None
    teams: bool | None = None
    enabled_weapons: list[str] = Field(default_factory=list)


class RoomIntentError(ValueError):
    """An explicit room setting cannot be resolved safely."""


DEFAULT_DISABLED_WEAPONS = ("machine_gun", "invincibility", "lob_sentry")
RANDOM_MAPS = tuple(
    arena
    for arena in ARENA_IDS
    if arena not in {
        "smash fort ctf",
        "smash island ctf",
        "graveyard ctf",
        "lava pit ctf",
        "sky arena temples ctf",
        "sky arena tunnels ctf",
    }
)


def _default_weapons(enabled_weapons: list[str]) -> int:
    mask = DEFAULT_WEAPONS
    for weapon in DEFAULT_DISABLED_WEAPONS:
        mask &= ~WEAPON_BITS[weapon]

    for weapon in enabled_weapons:
        canonical = weapon.strip().lower().replace(" ", "_").replace("-", "_")
        if canonical not in WEAPON_BITS:
            raise RoomIntentError(f"Unknown weapon: {weapon}")
        mask |= WEAPON_BITS[canonical]
    return mask


def resolve_room_intent(intent: RoomIntent, *, random_choice=None) -> dict:
    """Apply deterministic defaults and return arguments for create_room_url."""
    if not intent.is_room_request:
        raise RoomIntentError("Not a room request.")

    players = intent.players if intent.players is not None else 6
    if players not in VALID_MAX_PLAYERS:
        raise RoomIntentError(
            f"Player count must be one of: {sorted(VALID_MAX_PLAYERS)}."
        )

    mode = intent.mode
    if mode is None:
        mode = "ffa" if players <= 4 else "ctf"

    if intent.round_format is None:
        round_format = "timer"
        value = intent.value if intent.value is not None else 10
    else:
        round_format = intent.round_format
        if intent.value is None:
            if round_format == "score":
                raise RoomIntentError(
                    "Score target kitna chahiye? 1, 3, 6, 10 ya 20?"
                )
            raise RoomIntentError("Timer kitne minutes ka chahiye?")
        value = intent.value

    valid_values = VALID_TIMERS if round_format == "timer" else VALID_SCORE_TARGETS
    if value not in valid_values:
        label = "Timer" if round_format == "timer" else "Score target"
        raise RoomIntentError(
            f"{label} must be one of: {sorted(valid_values)}."
        )

    if intent.map:
        try:
            normalize_arena(intent.map)
        except ValueError as exc:
            raise RoomIntentError(str(exc)) from exc
        arena = intent.map
    else:
        chooser = random_choice or random.choice
        arena = chooser(RANDOM_MAPS)

    teams = True if intent.teams is None else intent.teams
    return {
        "game_mode": mode,
        "round_format": round_format,
        "value": value,
        "arena": arena,
        "max_players": players,
        "teams": teams if mode == "ffa" else False,
        "weapons": _default_weapons(intent.enabled_weapons),
    }


def create_room_from_intent(intent: RoomIntent, *, random_choice=None) -> str:
    """Resolve an intent and create an invitation URL."""
    return create_room_url(
        **resolve_room_intent(intent, random_choice=random_choice)
    )
