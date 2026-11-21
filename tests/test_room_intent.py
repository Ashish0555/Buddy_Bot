import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "api"))

from room_creator import (
    DEFAULT_WEAPONS,
    WEAPON_BITS,
    encode_ctf_mode,
    encode_ffa_mode,
)
from room_intent import (
    RoomIntent,
    RoomIntentError,
    _deterministic_room_intent,
    create_room_from_intent,
    resolve_room_intent,
)


def resolve(text_map=None, **kwargs):
    return resolve_room_intent(
        RoomIntent(is_room_request=True, map=text_map, **kwargs),
        random_choice=lambda options: options[0],
    )


def test_sf_four_defaults_to_ffa_with_teams():
    room = resolve("sf", players=4)
    assert room["game_mode"] == "ffa"
    assert room["max_players"] == 4
    assert room["teams"] is True


def test_explicit_ctf_is_preserved_for_four_players():
    room = resolve("sf", mode="ctf", players=4)
    assert room["game_mode"] == "ctf"
    assert room["max_players"] == 4


def test_bina_team_disables_ffa_teams():
    room = resolve("sf", players=4, teams=False)
    assert room["game_mode"] == "ffa"
    assert room["teams"] is False


def test_eight_players_defaults_to_ctf():
    room = resolve("sfc", players=8)
    assert room["game_mode"] == "ctf"
    assert room["max_players"] == 8


def test_room_without_settings_defaults_to_ctf_six_and_random_map():
    room = resolve(None)
    assert room["game_mode"] == "ctf"
    assert room["max_players"] == 6
    assert room["round_format"] == "timer"
    assert room["value"] == 10
    assert room["arena"]


def test_four_players_twenty_minutes_defaults_to_ffa_teams():
    room = resolve("sf", players=4, round_format="timer", value=20)
    assert room["game_mode"] == "ffa"
    assert room["teams"] is True
    assert room["value"] == 20


def test_ffa_without_timer_defaults_to_six_minutes():
    room = resolve("sf", mode="ffa", players=4)
    assert room["round_format"] == "timer"
    assert room["value"] == 6


def test_ffa_default_timer_is_encoded_in_room_url():
    url = create_room_from_intent(
        RoomIntent(is_room_request=True, map="sf", mode="ffa", players=4)
    )
    query = parse_qs(urlparse(url).query)
    assert query["mode"] == [str(encode_ffa_mode("timer", 6, teams=True, max_players=4))]


def test_ctf_without_timer_keeps_ten_minute_default():
    room = resolve("sfc", mode="ctf", players=6)
    assert room["round_format"] == "timer"
    assert room["value"] == 10


def test_explicit_timer_value_without_format_is_preserved():
    room = resolve("sf", players=4, value=20)
    assert room["round_format"] == "timer"
    assert room["value"] == 20


def test_invalid_player_count_is_helpful():
    with pytest.raises(RoomIntentError, match="Player count"):
        resolve("sf", players=5)


def test_score_without_target_asks_for_clarification():
    with pytest.raises(RoomIntentError, match="Score target"):
        resolve("sf", round_format="score")


def test_default_weapon_mask_disables_three_weapons():
    expected = DEFAULT_WEAPONS
    for weapon in ("machine_gun", "invincibility", "lob_sentry"):
        expected &= ~WEAPON_BITS[weapon]
    assert resolve("sf")["weapons"] == 159144
    assert resolve("sf")["weapons"] == expected


def test_explicitly_enabled_default_weapon_is_added_without_resetting_mask():
    room = resolve("sf", enabled_weapons=["machine_gun", "lob_sentry"])
    assert room["weapons"] == (159144 | WEAPON_BITS["machine_gun"] | WEAPON_BITS["lob_sentry"])


def test_unknown_map_is_not_guessed():
    with pytest.raises(RoomIntentError, match="Unknown/untested arena"):
        resolve("made up map")


@pytest.mark.parametrize(
    "text,expected_arena",
    [
        ("Steky FFA", "stekysspeedway"),
        ("FFA Steky", "stekysspeedway"),
        ("Steky", "stekysspeedway"),
        ("Steky's Speedway FFA", "stekysspeedway"),
        ("Skate FFA", "skatecity"),
        ("FFA Skate", "skatecity"),
    ],
)
def test_steky_and_skate_map_aliases_are_exact_and_order_independent(
    text, expected_arena
):
    intent = _deterministic_room_intent(text)
    assert intent is not None
    assert intent.map
    assert resolve_room_intent(intent)["arena"] == expected_arena


@pytest.mark.parametrize(
    "text,expected_mode,expected_arena",
    [
        ("Smash Fort CTF", "ctf", "smashfortctf"),
        ("CTF Smash Fort", "ctf", "smashfortctf"),
        ("Fort CTF", "ctf", "smashfortctf"),
        ("CTF Fort", "ctf", "smashfortctf"),
        ("Smash Fort FFA", "ffa", "smashfort"),
        ("FFA Smash Fort", "ffa", "smashfort"),
    ],
)
def test_smash_fort_mode_specific_aliases(
    text, expected_mode, expected_arena
):
    intent = _deterministic_room_intent(text)
    assert intent is not None
    assert intent.mode == expected_mode
    room = resolve_room_intent(intent)
    assert room["game_mode"] == expected_mode
    assert room["arena"] == expected_arena


def test_smash_fort_ctf_and_ffa_have_different_room_payload_arenas():
    ctf_url = create_room_from_intent(
        RoomIntent(is_room_request=True, map="smash fort ctf", mode="ctf")
    )
    ffa_url = create_room_from_intent(
        RoomIntent(is_room_request=True, map="smash fort", mode="ffa")
    )
    ctf_query = parse_qs(urlparse(ctf_url).query)
    ffa_query = parse_qs(urlparse(ffa_url).query)
    assert ctf_query["arena"] == ["smashfortctf"]
    assert ffa_query["arena"] == ["smashfort"]


@pytest.mark.parametrize(
    "text,expected_format,expected_value",
    [
        ("Lava Pit 10 min", "timer", 10),
        ("FFA Lava Pit 10 minutes", "timer", 10),
        ("Lava Pit ST 3", "score", 3),
        ("Lava Pit ST 3 10 min", "timer", 10),
        ("Lava Pit 10 min ST 3", "timer", 10),
        ("Lava Pit FFA", "timer", 6),
    ],
)
def test_natural_language_timer_and_score_target_settings(
    text, expected_format, expected_value
):
    intent = _deterministic_room_intent(text)
    assert intent is not None
    room = resolve_room_intent(intent)
    assert room["round_format"] == expected_format
    assert room["value"] == expected_value


def test_timer_and_score_target_are_parsed_independently():
    intent = _deterministic_room_intent("Lava Pit 10 min ST 3")
    assert intent is not None
    room = resolve_room_intent(intent)
    assert room["match_duration"] == 10
    assert room["score_target"] == 3
    assert room["round_format"] == "timer"
    assert room["value"] == 10


@pytest.mark.parametrize(
    "text,expected_mode",
    [
        ("Lava Pit 10 min", "ctf"),
        ("FFA Lava Pit 10 minutes", "ffa"),
        ("Lava Pit FFA", "ffa"),
    ],
)
def test_explicit_timer_settings_reach_room_payload(text, expected_mode):
    intent = _deterministic_room_intent(text)
    assert intent is not None
    url = create_room_from_intent(intent)
    query = parse_qs(urlparse(url).query)
    assert query["arena"] == ["lavapitctf"]
    room = resolve_room_intent(intent)
    if expected_mode == "ffa":
        expected_mode_code = encode_ffa_mode(
            room["round_format"],
            room["value"],
            teams=room["teams"],
            max_players=room["max_players"],
        )
    else:
        expected_mode_code = encode_ctf_mode(
            room["round_format"],
            room["value"],
            max_players=room["max_players"],
        )
    assert query["mode"] == [str(expected_mode_code)]
