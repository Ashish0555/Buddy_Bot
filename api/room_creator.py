"""
Smash Karts private-room URL builder.

This module ONLY builds the CrazyGames invite URL.
It does not connect to Photon and does not create the room itself.

Verified from controlled room-link tests:
- India room IDs work with the format: in3 + 5 digits.
- mdfrs=0 means no modifiers.
- wpns is a weapon bitmask.
- mode encodes round format/value and Teams.
- arena is the map identifier.

NOTE:
The FFA timer/score-target mode formulas below are based on the
controlled tests we performed. CTF mode encoding is intentionally left
as an explicit override until its exact mode family is verified.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from urllib.parse import urlencode


BASE_URL = "https://smashkarts.io/link/"


# ------------------------------------------------------------------
# ROOM ID
# ------------------------------------------------------------------

def generate_india_room_id() -> str:
    """Generate a compatible-looking India-region room id: in3xxxxx."""
    return f"in3{random.randint(0, 99_999):05d}"


# ------------------------------------------------------------------
# MODE ENCODING
# ------------------------------------------------------------------

VALID_TIMERS = {3, 6, 10, 20}
VALID_SCORE_TARGETS = {1, 3, 6, 10, 20}
VALID_MAX_PLAYERS = {2, 4, 6, 8, 12, 16, 20, 24}

TIMER_SHIFT = 8
SCORE_TARGET_SHIFT = 14
MAX_PLAYERS_SHIFT = 24

FFA_TIMER_CODE = 0x08
FFA_SCORE_CODE = 0x0C
CTF_TIMER_CODE = 0x3A
CTF_SCORE_CODE = 0x3E
FFA_TEAMS_BIT = 0x02

MAX_PLAYER_CODE = {
    8: 0,
    12: 1,
    16: 2,
    20: 3,
    24: 4,
    2: 5,
    4: 6,
    6: 7,
}


def _max_players_field(max_players: int) -> int:
    if max_players not in VALID_MAX_PLAYERS:
        raise ValueError(
            f"Max players must be one of: {sorted(VALID_MAX_PLAYERS)}"
        )
    return MAX_PLAYER_CODE[max_players] << MAX_PLAYERS_SHIFT


def encode_ffa_mode(
    round_format: str,
    value: int,
    teams: bool = False,
    max_players: int = 24,
) -> int:
    fmt = round_format.strip().lower()

    if fmt == "timer":
        if value not in VALID_TIMERS:
            raise ValueError("Timer must be 3, 6, 10, or 20 minutes.")
        low = (value << TIMER_SHIFT) | FFA_TIMER_CODE
    elif fmt in {"score", "score_target", "score-target"}:
        if value not in VALID_SCORE_TARGETS:
            raise ValueError("Score target must be 1, 3, 6, 10, or 20.")
        low = (value << SCORE_TARGET_SHIFT) | FFA_SCORE_CODE
    else:
        raise ValueError("round_format must be 'timer' or 'score'.")

    mode = _max_players_field(max_players) | low
    if teams:
        mode |= FFA_TEAMS_BIT
    return mode


def encode_ctf_mode(
    round_format: str,
    value: int,
    max_players: int = 8,
) -> int:
    fmt = round_format.strip().lower()

    if fmt == "timer":
        if value not in VALID_TIMERS:
            raise ValueError("Timer must be 3, 6, 10, or 20 minutes.")
        low = (value << TIMER_SHIFT) | CTF_TIMER_CODE
    elif fmt in {"score", "score_target", "score-target"}:
        if value not in VALID_SCORE_TARGETS:
            raise ValueError("Score target must be 1, 3, 6, 10, or 20.")
        low = (value << SCORE_TARGET_SHIFT) | CTF_SCORE_CODE
    else:
        raise ValueError("round_format must be 'timer' or 'score'.")

    return _max_players_field(max_players) | low


def encode_mode(
    game_mode: str,
    round_format: str | None = None,
    value: int | None = None,
    teams: bool = False,
    max_players: int = 24,
    mode_override: int | None = None,
) -> int:
    if mode_override is not None:
        return int(mode_override)
    if round_format is None or value is None:
        raise ValueError("round_format and value are required.")

    gm = game_mode.strip().lower()
    if gm == "ffa":
        return encode_ffa_mode(round_format, value, teams=teams, max_players=max_players)
    if gm == "ctf":
        return encode_ctf_mode(round_format, value, max_players=max_players)
    raise ValueError("game_mode must be 'ffa' or 'ctf'.")


# ------------------------------------------------------------------
# WEAPON ENCODING
# ------------------------------------------------------------------

DEFAULT_WEAPONS = 159_212

WEAPON_BITS = {
    "machine_gun": 4,
    "rocket": 8,
    "mines": 32,
    "invincibility": 64,
    "fake_lootbox": 128,
    "nuke": 256,
    "bullets": 1_024,
    "canons": 2_048,
    "lob_grenuke": 8_192,
    "spikys": 16_384,
    "snowball_blaster": 131_072,
    "lob_sentry": 8_388_608,
}

WEAPON_ALIASES = {
    "machine gun": "machine_gun",
    "machinegun": "machine_gun",
    "rocket": "rocket",
    "rockets": "rocket",
    "mine": "mines",
    "mines": "mines",
    "invincibility": "invincibility",
    "fake lootbox": "fake_lootbox",
    "fake loot box": "fake_lootbox",
    "nuke": "nuke",
    "bullets": "bullets",
    "bullet": "bullets",
    "canons": "canons",
    "cannons": "canons",
    "cannon": "canons",
    "lob grenuke": "lob_grenuke",
    "lob-grenuke": "lob_grenuke",
    "grenuke": "lob_grenuke",
    "spikys": "spikys",
    "spiky": "spikys",
    "snowball blaster": "snowball_blaster",
    "snowball": "snowball_blaster",
    "lob sentry": "lob_sentry",
    "lob-sentry": "lob_sentry",
}


def normalize_weapon_name(name: str) -> str:
    key = " ".join(name.strip().lower().split())
    if key not in WEAPON_ALIASES:
        raise ValueError(f"Unknown weapon: {name}")
    return WEAPON_ALIASES[key]


def encode_weapons(
    enabled_weapons: list[str] | tuple[str, ...] | set[str] | None = None,
) -> int:
    """
    Build the wpns bitmask from the enabled weapon list.

    Pass None or an empty list to use the verified default setup:
    wpns=159212.

    IMPORTANT:
    The game's default setup has Lob Sentry OFF. All other listed weapons
    are ON in the default mask.
    """
    if not enabled_weapons:
        return DEFAULT_WEAPONS

    mask = 0
    for weapon in enabled_weapons:
        canonical = normalize_weapon_name(weapon)
        mask |= WEAPON_BITS[canonical]

    return mask


def disable_weapons(
    disabled_weapons: list[str] | tuple[str, ...] | set[str],
) -> int:
    """Start from the verified default mask and disable selected weapons."""
    mask = DEFAULT_WEAPONS

    for weapon in disabled_weapons:
        canonical = normalize_weapon_name(weapon)
        mask &= ~WEAPON_BITS[canonical]

    return mask


def enable_default_plus(
    extra_weapons: list[str] | tuple[str, ...] | set[str],
) -> int:
    """Start from default and enable additional weapons (e.g. Lob Sentry)."""
    mask = DEFAULT_WEAPONS

    for weapon in extra_weapons:
        canonical = normalize_weapon_name(weapon)
        mask |= WEAPON_BITS[canonical]

    return mask


# ------------------------------------------------------------------
# ARENA / MAP IDs VERIFIED FROM TESTED LINKS
# ------------------------------------------------------------------

ARENA_IDS = {
    # Standard Arena Maps
    "smash island": "smashisland",
    "gravel pit": "gravelpit",
    "steky's speedway": "stekysspeedway",
"stekys speedway": "stekysspeedway",
"stekysspeedway": "stekysspeedway",
    "smash fort": "smashfort",
    "slick 'n slide": "slicknslide",
    "slick n slide": "slicknslide",
    "snowpark": "snowpark",
    "snow shrine": "snowshrine",
    "lava pit": "lavapitctf",
    "boneyard basin": "boneyardbasin",
    "toxic towers": "toxictowers",
    "sand box": "sandbox",
    "sandbox": "sandbox",

    # Graveyard Maps
    "the graveyard": "graveyard",
    "graveyard": "graveyard",
    "the old graveyard": "oldgraveyard",
    "graveyard ffa": "graveyardffa",

    # Sky Arena Series
    "dropzone": "skyarena-dropzone",
    "pinball": "skyarena-pinball",
    "showdown": "skyarena-showdown",
    "temples": "skyarena-temples",
    "tunnels": "skyarena-tunnels",

    # CTF Maps
    "smash fort ctf": "smashfort",
    "smash island ctf": "smashisland",
    "graveyard ctf": "graveyard",
    "lava pit ctf": "lavapitctf",
    "sky arena temples ctf": "skyarena-temples",
    "sky arena tunnels ctf": "skyarena-tunnels",

    # Space
    "space station": "spacestations",
    "space stations": "spacestations",

    # Existing/known aliases
    "sky arena showdown": "skyarena-showdown",
    "sky arena dropzone": "skyarena-dropzone",
    "sky arena pinball": "skyarena-pinball",
    "sky arena temples": "skyarena-temples",
    "sky arena tunnels": "skyarena-tunnels",
    "snowshrine": "snowshrine",
    "snow shrine": "snowshrine",
    "skate city": "skatecity",
}

MAP_SHORTFORMS = {
    "sf": "smash fort",
    "si": "smash island",
    "gp": "gravel pit",
    "speedway": "stekysspeedway",
    "sns": "slicknslide",
    "sp": "snowpark",
    "sr": "snow shrine",
    "lp": "lava pit",
    "bb": "boneyard basin",
    "tt": "toxic towers",
    "sb": "sand box",
    "gy": "the graveyard",
    "og": "the old graveyard",
    "gffa": "graveyard ffa",
    "dz": "dropzone",
    "pp": "pinball",
    "sd": "showdown",
    "te": "temples",
    "t": "tunnels",
    "tu": "tunnels",
    "sfc": "smash fort ctf",
    "sic": "smash island ctf",
    "gyc": "graveyard ctf",
    "lpc": "lava pit ctf",
    "tec": "sky arena temples ctf",
    "tuc": "sky arena tunnels ctf",
    "ss": "space station",
    "space": "space station",
}


def normalize_arena(arena: str) -> str:
    """Convert a map name or supported short form to the arena ID."""
    key = " ".join(
        arena.strip().lower().replace("_", " ").split()
    )
    key = MAP_SHORTFORMS.get(key, key)

    if key in ARENA_IDS:
        return ARENA_IDS[key]

    if key in set(ARENA_IDS.values()):
        return key

    raise ValueError(f"Unknown/untested arena: {arena}")


# ------------------------------------------------------------------
# URL
# ------------------------------------------------------------------

@dataclass(frozen=True)
class RoomConfig:
    room_id: str
    mode: int
    weapons: int
    arena: str
    modifiers: int = 0


def build_room_url(config: RoomConfig) -> str:
    """Build the Smash Karts invite URL in the canonical format."""
    params = {
        "mode": str(config.mode),
        "wpns": str(config.weapons),
        "room": config.room_id,
        "arena": config.arena,
        "mdfrs": str(config.modifiers),
    }
    return f"{BASE_URL}?{urlencode(params)}"


def create_room_url(
    *,
    game_mode: str,
    round_format: str,
    value: int,
    arena: str,
    max_players: int = 24,
    teams: bool = False,
    weapons: int = DEFAULT_WEAPONS,
    room_id: str | None = None,
) -> str:
    gm = game_mode.strip().lower()

    if room_id is None:
        room_id = generate_india_room_id()

    if gm == "ctf" and max_players == 24:
        max_players = 8

    mode = encode_mode(
        gm,
        round_format=round_format,
        value=value,
        teams=teams,
        max_players=max_players,
    )

    return build_room_url(
        RoomConfig(
            room_id=room_id,
            mode=mode,
            weapons=weapons,
            arena=normalize_arena(arena),
            modifiers=0,
        )
    )


def create_ffa_room_url(**kwargs) -> str:
    kwargs = dict(kwargs)
    kwargs.setdefault("max_players", 24)
    return create_room_url(game_mode="ffa", **kwargs)


def create_ctf_room_url(**kwargs) -> str:
    kwargs = dict(kwargs)
    kwargs.setdefault("max_players", 8)
    kwargs.pop("teams", None)
    return create_room_url(game_mode="ctf", **kwargs)


__all__ = [
    "DEFAULT_WEAPONS",
    "WEAPON_BITS",
    "ARENA_IDS",
    "MAP_SHORTFORMS",
    "RoomConfig",
    "generate_india_room_id",
    "encode_ffa_mode",
    "encode_ctf_mode",
    "encode_mode",
    "encode_weapons",
    "disable_weapons",
    "enable_default_plus",
    "normalize_arena",
    "build_room_url",
    "create_room_url",
    "create_ffa_room_url",
    "create_ctf_room_url",
]
