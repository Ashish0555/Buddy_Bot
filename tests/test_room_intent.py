import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "api"))

from room_creator import DEFAULT_WEAPONS, WEAPON_BITS
from room_intent import RoomIntent, RoomIntentError, resolve_room_intent


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
    room = resolve("dz", players=8)
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
