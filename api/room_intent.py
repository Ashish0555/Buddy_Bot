"""Natural-language Smash Karts room intent extraction and resolution."""

from __future__ import annotations

import re
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
    match_duration: int | None = None
    score_target: int | None = None
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


def _mode_aware_map_name(map_name: str, mode: str) -> str:
    """Convert shared map names to the variant requested by the game mode."""
    normalized = _normalize_map_name(map_name)

    if mode == "ctf":
        if normalized == "smash fort" or normalized == "sf":
            return "smash fort ctf"
        if normalized == "smash island":
            return "smash island ctf"
        if normalized in {"graveyard", "the graveyard"}:
            return "graveyard ctf"
        if normalized == "lava pit":
            return "lava pit ctf"
        if normalized == "temples":
            return "sky arena temples ctf"
        if normalized == "tunnels":
            return "sky arena tunnels ctf"
    else:
        if normalized == "smash fort ctf":
            return "smash fort"
        if normalized == "smash island ctf":
            return "smash island"
        if normalized == "graveyard ctf":
            return "the graveyard"
        if normalized == "lava pit ctf":
            return "lava pit"
        if normalized == "sky arena temples ctf":
            return "temples"
        if normalized == "sky arena tunnels ctf":
            return "tunnels"

    return normalized


_TEXT_MAP_ALIASES = (
    ("smash fort ctf", "smash fort ctf"),
    ("ctf smash fort", "smash fort ctf"),
    ("fort ctf", "smash fort ctf"),
    ("ctf fort", "smash fort ctf"),
    ("smash fort ffa", "smash fort"),
    ("ffa smash fort", "smash fort"),
    ("smash fort", "smash fort"),
    ("steky's speedway", "steky's speedway"),
    ("stekys speedway", "steky's speedway"),
    ("stekysspeedway", "steky's speedway"),
    ("steky", "steky's speedway"),
    ("skate city", "skate city"),
    ("skate", "skate city"),
)


def _extract_map_from_text(text: str) -> str | None:
    """Resolve explicit map names and aliases without fuzzy matching."""
    normalized = " ".join(
        text.casefold().replace("_", " ").replace("'", "").split()
    )
    for alias, map_name in _TEXT_MAP_ALIASES:
        alias = alias.replace("'", "")
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            return map_name
    return None


def _extract_round_settings(text: str) -> dict:
    """Extract timer and score target as independent settings."""
    result: dict[str, int | str] = {}

    timer_match = re.search(
        r"\b(\d+)\s*(?:min|mins|minute|minutes)\b",
        text.lower(),
    )
    if timer_match:
        result["match_duration"] = int(timer_match.group(1))

    tm_match = re.search(r"\btm\s*(\d+)\b", text.lower())
    if tm_match:
        result["match_duration"] = int(tm_match.group(1))

    score_match = re.search(r"\bst\s*(\d+)\b", text.lower())
    if score_match:
        result["score_target"] = int(score_match.group(1))

    if "match_duration" in result:
        result["round_format"] = "timer"
        result["value"] = int(result["match_duration"])
    elif "score_target" in result:
        result["round_format"] = "score"
        result["value"] = int(result["score_target"])

    return result


def _deterministic_room_intent(text: str) -> RoomIntent | None:
    """Deterministically extract room intent from clear gaming requests.
    
    Handles patterns like:
    - "send skate city st20 ctf map"
    - "create SF CTF map"
    - "make CTF in skate city"
    - "dz me 2 bande bina team"
    - "send SF CTF map"
    - "give me a 6 player match"
    
    Returns RoomIntent if high-confidence room request detected, None otherwise.
    """
    normalized = " ".join(text.casefold().split())
    
    # Check for explicit non-room patterns first
    if re.search(r"\b(?:banaya|banaaya|made|created)\s+tha\b", normalized):
        return None
    if re.search(r"\b(?:room|match)\b.*\b(?:kaise|how|system|meaning)\b", normalized):
        return None
    if re.search(r"\b(?:what|how|why|kya|kaise)\b.*\b(?:is|hai|hota|works?)\b", normalized):
        return None
    if re.search(r"\b(?:tell|show|picture|image|photo)\b", normalized):
        return None
    
    # Look for strong room request indicators
    has_action = bool(re.search(
        r"\b(?:send|create|make|give|generate|build|khol|open|bnao|bna|bnado|bnade|bna\s+de|"
        r"room\s+bna|match\s+bna|match\s+create|room\s+create)\b",
        normalized
    ))
    has_room_noun = bool(re.search(r"\b(?:room|match)\b", normalized))
    has_mode = bool(re.search(r"\b(?:ctf|ffa)\b", normalized))
    has_map_shortform = bool(re.search(
        r"\b(?:sf|si|gp|dz|lp|pp|sd|te|tu|t|ss|gy|sns)\b", normalized
    ))
    has_map_name = bool(re.search(
        r"\b(?:skate\s+city|smash\s+fort|smash\s+island|gravel\s+pit|dropzone|lava\s+pit|"
        r"graveyard|sky\s+arena|pinball|showdown|temples|tunnels|space\s+station|"
        r"battle\s+box|toxic\s+tower|snowpark|snow\s+shrine|steky|slick|boneyard|toxic|"
        r"sand\s+box|old\s+graveyard)\b",
        normalized
    ))
    has_player_count = bool(re.search(r"\b\d+\s*(?:player|players|bande|bandon|logo|logon)\b", normalized))
    has_team_setting = bool(re.search(r"\b(?:team|bina\s+team|without\s+team|no\s+team)\b", normalized))
    has_abbreviated = bool(re.search(r"\b(?:st|tm)\d+\b", normalized))
    has_match_duration = bool(re.search(
        r"\b\d+\s*(?:min|mins|minute|minutes)\b",
        normalized,
    ))
    has_score_target = bool(re.search(r"\bst\s*\d+\b", normalized))
    has_random = bool(re.search(r"\b(?:random|any)\b", normalized))
    
    # High confidence if we have action + (mode or map or room noun) OR player count + mode/map/room noun
    is_high_confidence = (
        (has_action and (has_mode or has_map_shortform or has_map_name or has_room_noun)) or
        (has_player_count and (has_mode or has_map_shortform or has_map_name or has_team_setting or has_room_noun)) or
        (has_mode and has_map_name) or
        (_extract_map_from_text(normalized) is not None and has_mode) or
        (_extract_map_from_text(normalized) is not None and not has_mode and not has_action
         and len(normalized.split()) <= 3) or
        (has_random and has_mode) or
        (has_abbreviated and (has_mode or has_map_shortform or has_map_name)) or
        (has_match_duration and has_map_name) or
        (has_score_target and has_map_name)
    )
    
    if not is_high_confidence:
        return None
    
    # Extract mode before the map so mode-specific variants take priority.
    mode = None
    if re.search(r"\bctf\b", normalized):
        mode = "ctf"
    elif re.search(r"\bffa\b", normalized):
        mode = "ffa"

    # Extract map
    map_name = None
    explicit_map = _extract_map_from_text(normalized)
    if explicit_map is not None:
        map_name = explicit_map
    else:
        # Check exact CTF/FFA variants before shared short names.
        shortform_match = re.search(
            r"\b(sfc|sic|gyc|lpc|tec|tuc|sf|si|gp|dz|lp|pp|sd|te|tu|t|ss|gy|sns)\b",
            normalized
        )
        if shortform_match:
            map_name = shortform_match.group(1)
        else:
            map_patterns = [
                (r"\bsmash\s+fort\s+ctf\b", "smash fort ctf"),
                (r"\bctf\s+smash\s+fort\b", "smash fort ctf"),
                (r"\bfort\s+ctf\b", "smash fort ctf"),
                (r"\bctf\s+fort\b", "smash fort ctf"),
                (r"\bsmash\s+fort\s+ffa\b", "smash fort"),
                (r"\bffa\s+smash\s+fort\b", "smash fort"),
                (r"\bsmash\s+fort\b", "smash fort"),
                (r"\bskate\s+city\b", "skate city"),
                (r"\bsmash\s+island\b", "smash island"),
                (r"\bgravel\s+pit\b", "gravel pit"),
                (r"\bdropzone\b", "dropzone"),
                (r"\blava\s+pit\b", "lava pit"),
                (r"\bgraveyard\b", "the graveyard"),
                (r"\bsky\s+arena\s+temples\b", "sky arena temples"),
                (r"\bsky\s+arena\s+tunnels\b", "sky arena tunnels"),
                (r"\bsky\s+arena\s+showdown\b", "sky arena showdown"),
                (r"\bsky\s+arena\s+pinball\b", "sky arena pinball"),
                (r"\bsky\s+arena\s+dropzone\b", "dropzone"),
                (r"\bpinball\b", "pinball"),
                (r"\bshowdown\b", "showdown"),
                (r"\btemples\b", "temples"),
                (r"\btunnels\b", "tunnels"),
                (r"\bspace\s+station\b", "space station"),
                (r"\bbattle\s+box\b", "battle box"),
                (r"\btoxic\s+towers?\b", "toxic towers"),
                (r"\bsnowpark\b", "snowpark"),
                (r"\bsnow\s+shrine\b", "snow shrine"),
                (r"\bsteky\b", "steky's speedway"),
                (r"\bslick\b", "slick 'n slide"),
                (r"\bboneyard\b", "boneyard basin"),
                (r"\bold\s+graveyard\b", "the old graveyard"),
                (r"\bsand\s+box\b", "sand box"),
            ]
            for pattern, name in map_patterns:
                if re.search(pattern, normalized):
                    map_name = name
                    break

    if map_name is not None and mode is not None:
        map_name = _mode_aware_map_name(map_name, mode)
    
    # Extract players
    players = None
    player_match = re.search(r"\b(\d+)\s*(?:player|players|bande|bandon|logo|logon)\b", normalized)
    if player_match:
        players = int(player_match.group(1))
    
    # Extract team setting
    teams = None
    if re.search(r"\bbina\s+team\b|\bwithout\s+team\b|\bno\s+team\b", normalized):
        teams = False
    elif re.search(r"\bteam\b", normalized) and not re.search(r"\bbina\s+team\b|\bwithout\s+team\b|\bno\s+team\b", normalized):
        teams = True
    
    # Extract timer and score target independently, regardless of word order.
    round_settings = _extract_round_settings(normalized)
    match_duration = round_settings.get("match_duration")
    score_target = round_settings.get("score_target")
    round_format = round_settings.get("round_format")
    value = round_settings.get("value")
    
    # If we have high confidence, return the intent (even with just defaults)
    # This handles generic "make a room" requests
    if is_high_confidence:
        return RoomIntent(
            is_room_request=True,
            map=map_name,
            mode=mode,
            players=players,
            round_format=round_format,
            value=value,
            match_duration=match_duration,
            score_target=score_target,
            teams=teams,
            enabled_weapons=[],
        )
    
    return None


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
        # Canonicalize aliases before checking CTF verification.
        map_name = _mode_aware_map_name(intent.map, intent.mode) if intent.mode is not None else _normalize_map_name(intent.map)
        try:
            arena_id = _get_arena_id(map_name)
            map_is_ctf_verified = (
                arena_id in CTF_VERIFIED_ARENA_IDS
                or map_name in {"smash fort", "smash fort ctf"}
            )
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

    # Select the mode-specific map variant before the final arena lookup.
    if map_provided:
        assert intent.map is not None
        mode_map_name = _mode_aware_map_name(intent.map, mode)
        try:
            arena = normalize_arena(mode_map_name)
        except ValueError as exc:
            raise RoomIntentError(str(exc)) from exc
    else:
        arena = None

    match_duration = intent.match_duration
    score_target = intent.score_target

    if match_duration is None and intent.round_format == "timer" and intent.value is not None:
        match_duration = intent.value
    if score_target is None and intent.round_format == "score" and intent.value is not None:
        score_target = intent.value

    if match_duration is not None:
        round_format = "timer"
        value = match_duration
    elif score_target is not None:
        round_format = "score"
        value = score_target
    elif intent.round_format is None:
        round_format = "timer"
        if intent.value is not None:
            value = intent.value
        else:
            value = 6 if mode == "ffa" else 10
    else:
        round_format = intent.round_format
        if intent.value is None:
            if round_format == "score":
                raise RoomIntentError(
                    "Score target kitna chahiye? 1, 3, 6, 10 ya 20?"
                )
            raise RoomIntentError("Timer kitne minutes ka chahiye?")
        value = intent.value

    if match_duration is not None and match_duration not in VALID_TIMERS:
        raise RoomIntentError(
            f"Timer must be one of: {sorted(VALID_TIMERS)}."
        )
    if score_target is not None and score_target not in VALID_SCORE_TARGETS:
        raise RoomIntentError(
            f"Score target must be one of: {sorted(VALID_SCORE_TARGETS)}."
        )

    valid_values = VALID_TIMERS if round_format == "timer" else VALID_SCORE_TARGETS
    if value not in valid_values:
        label = "Timer" if round_format == "timer" else "Score target"
        raise RoomIntentError(
            f"{label} must be one of: {sorted(valid_values)}."
        )

    # Determine arena with mode-aware random selection
    if arena is None:
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
        "match_duration": match_duration,
        "score_target": score_target,
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


__all__ = [
    "RoomIntent",
    "RoomIntentError",
    "resolve_room_intent",
    "create_room_from_intent",
    "_deterministic_room_intent",
]