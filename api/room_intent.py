"""Natural-language Smash Karts room intent extraction and resolution."""

from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    from .room_creator import (
        ARENA_IDS,
        CTF_ALLOWLIST,
        CTF_VERIFIED_MAPS,
        CTF_VERIFIED_ARENA_IDS,
        DEFAULT_WEAPONS,
        FFA_EXCLUDED_MAPS,
        MAP_SHORTFORMS,
        VALID_MAX_PLAYERS,
        VALID_SCORE_TARGETS,
        VALID_TIMERS,
        WEAPON_BITS,
        create_room_url,
        get_ctf_verified_arenas,
        get_ffa_arenas,
        is_ctf_allowlisted,
        is_ctf_verified,
        normalize_arena,
    )
except ImportError:
    from room_creator import (
        ARENA_IDS,
        CTF_ALLOWLIST,
        CTF_VERIFIED_MAPS,
        CTF_VERIFIED_ARENA_IDS,
        DEFAULT_WEAPONS,
        FFA_EXCLUDED_MAPS,
        MAP_SHORTFORMS,
        VALID_MAX_PLAYERS,
        VALID_SCORE_TARGETS,
        VALID_TIMERS,
        WEAPON_BITS,
        create_room_url,
        get_ctf_verified_arenas,
        get_ffa_arenas,
        is_ctf_allowlisted,
        is_ctf_verified,
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


def _normalize_map_name(name: str) -> str:
    """Normalize a map name for consistent comparison."""
    key = " ".join(name.strip().lower().replace("_", " ").split())
    return MAP_SHORTFORMS.get(key, key)


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


def _get_arena_id(map_name: str) -> str:
    """Get the arena ID for a map name, normalizing first."""
    normalized = _normalize_map_name(map_name)
    return normalize_arena(normalized)


def resolve_room_intent(intent: RoomIntent, *, random_choice=None) -> dict:
    """Apply deterministic defaults and return arguments for create_room_url."""
    if not intent.is_room_request:
        raise RoomIntentError("Not a room request.")

    players = intent.players if intent.players is not None else 6
    if players not in VALID_MAX_PLAYERS:
        raise RoomIntentError(
            f"Player count must be one of: {sorted(VALID_MAX_PLAYERS)}."
        )

    # Determine mode with CTF map compatibility validation
    mode = intent.mode
    map_provided = intent.map is not None

    # Check CTF compatibility via arena ID (not just map name)
    # because multiple map names can share the same arena ID
    arena_id = None
    map_is_ctf_verified = False
    map_is_ctf_allowlisted = False

    if map_provided:
        assert intent.map is not None
        map_name = intent.map
        try:
            arena_id = _get_arena_id(map_name)
            map_is_ctf_verified = arena_id in CTF_VERIFIED_ARENA_IDS
            map_is_ctf_allowlisted = is_ctf_allowlisted(map_name)
        except ValueError as exc:
            raise RoomIntentError(str(exc)) from exc

    if mode is None:
        # Mode unspecified - apply defaults with map compatibility
        if map_provided:
            if map_is_ctf_verified:
                # CTF-compatible map: normal default rules apply
                mode = "ffa" if players <= 4 else "ctf"
            elif map_is_ctf_allowlisted:
                # Map is in CTF allowlist but not verified - default to FFA
                mode = "ffa"
            else:
                # Non-CTF map: force FFA regardless of player count
                mode = "ffa"
        else:
            # No map specified: normal default rules apply
            mode = "ffa" if players <= 4 else "ctf"
    else:
        # Mode explicitly specified
        if mode == "ctf" and map_provided:
            if map_is_ctf_verified:
                # Valid CTF map (arena ID is verified)
                pass
            elif map_is_ctf_allowlisted:
                # In allowlist but not verified
                raise RoomIntentError(
                    f"CTF on '{intent.map}' is not yet verified. "
                    "Game-tested evidence required before enabling."
                )
            else:
                # Not a CTF map at all
                raise RoomIntentError(
                    f"'{intent.map}' does not support CTF mode. "
                    "Use FFA or pick a CTF-compatible map."
                )

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

    # Determine arena with mode-aware random selection
    if intent.map:
        try:
            normalize_arena(intent.map)
        except ValueError as exc:
            raise RoomIntentError(str(exc)) from exc
        arena = intent.map
    else:
        chooser = random_choice or random.choice
        if mode == "ctf":
            # Random CTF map - only from verified CTF maps
            arena = chooser(get_ctf_verified_arenas())
        else:
            # Random FFA map - from FFA-compatible maps
            arena = chooser(get_ffa_arenas())

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