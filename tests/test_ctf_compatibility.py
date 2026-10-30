"""Tests for CTF map compatibility and new game mode rules."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "api"))

from room_creator import (
    CTF_ALLOWLIST,
    CTF_VERIFIED_MAPS,
    CTF_VERIFIED_ARENA_IDS,
    FFA_EXCLUDED_MAPS,
    MAP_SHORTFORMS,
    ARENA_IDS,
    get_ctf_verified_arenas,
    get_ffa_arenas,
    is_ctf_verified,
    is_ctf_allowlisted,
    normalize_arena,
)
from room_intent import RoomIntent, RoomIntentError, resolve_room_intent


def resolve(map_name=None, mode=None, players=None, **kwargs):
    """Helper to resolve room intent with defaults."""
    return resolve_room_intent(
        RoomIntent(
            is_room_request=True,
            map=map_name,
            mode=mode,
            players=players,
            **kwargs
        ),
        random_choice=lambda options: options[0],
    )


class TestCTFVerifiedMaps:
    """Test that all 6 verified CTF maps are properly recognized."""

    @pytest.mark.parametrize("map_name,expected_arena", [
        ("smash fort ctf", "smashfort"),
        ("smash island ctf", "smashisland"),
        ("graveyard ctf", "graveyard"),
        ("lava pit ctf", "lavapitctf"),
        ("sky arena temples ctf", "skyarena-temples"),
        ("sky arena tunnels ctf", "skyarena-tunnels"),
    ])
    def test_ctf_verified_map_normalization(self, map_name, expected_arena):
        """Verified CTF maps normalize to correct arena IDs."""
        assert normalize_arena(map_name) == expected_arena

    @pytest.mark.parametrize("map_name", [
        "smash fort ctf",
        "smash island ctf",
        "graveyard ctf",
        "lava pit ctf",
        "sky arena temples ctf",
        "sky arena tunnels ctf",
    ])
    def test_ctf_verified_map_is_recognized(self, map_name):
        """Verified CTF maps return True for is_ctf_verified."""
        assert is_ctf_verified(map_name) is True

    @pytest.mark.parametrize("shortform,expected_full", [
        ("sfc", "smash fort ctf"),
        ("sic", "smash island ctf"),
        ("gyc", "graveyard ctf"),
        ("lpc", "lava pit ctf"),
        ("tec", "sky arena temples ctf"),
        ("tuc", "sky arena tunnels ctf"),
    ])
    def test_ctf_shortforms_work(self, shortform, expected_full):
        """CTF shortforms resolve to correct full map names."""
        assert MAP_SHORTFORMS[shortform] == expected_full


class TestCTFAllowlistMaps:
    """Test the complete 12-map CTF allowlist."""

    @pytest.mark.parametrize("map_name", [
        "battle box",
        "skate city",
        "toxic tower",
        "toxic towers",
        "space station",
        "space stations",
        "sky arena showdown",
        "smash island",
        "snowpark ctf",
        "lava pit ctf",
        "graveyard ctf",
        "smash fort ctf",
    ])
    def test_ctf_allowlist_contains_map(self, map_name):
        """All 12 user-specified CTF maps are in allowlist."""
        assert is_ctf_allowlisted(map_name) is True

    def test_allowlist_has_exactly_12_maps(self):
        """CTF allowlist contains exactly 12 unique maps."""
        # Note: "smash island" and "smash island ctf" are the same map (same arena ID)
        # The user listed 12 maps; "Smash Island" is #8, not a separate CTF variant
        assert len(CTF_ALLOWLIST) == 12


class TestMapsSharingCTFVerifiedArenaIDs:
    """Maps that share arena IDs with CTF-verified maps are CTF-compatible."""

    @pytest.mark.parametrize("map_name", [
        "temples",           # shares skyarena-temples with sky arena temples ctf
        "tunnels",           # shares skyarena-tunnels with sky arena tunnels ctf
        "the graveyard",     # shares graveyard with graveyard ctf
        "smash island",      # shares smashisland with smash island ctf
        "smash fort",        # shares smashfort with smash fort ctf
        "lava pit",          # shares lavapitctf with lava pit ctf
    ])
    def test_map_sharing_ctf_arena_is_ctf_verified(self, map_name):
        """Maps sharing arena IDs with CTF-verified maps are CTF-compatible."""
        assert is_ctf_verified(map_name) is True

    @pytest.mark.parametrize("map_name", [
        "temples",
        "tunnels",
        "the graveyard",
        "smash island",
        "smash fort",
        "lava pit",
    ])
    def test_map_sharing_ctf_arena_defaults_to_ctf_with_6_players(self, map_name):
        """Maps sharing CTF arena IDs default to CTF with 6+ players."""
        room = resolve(map_name, players=6)
        assert room["game_mode"] == "ctf"

    @pytest.mark.parametrize("map_name", [
        "temples",
        "tunnels",
        "the graveyard",
        "smash island",
        "smash fort",
        "lava pit",
    ])
    def test_map_sharing_ctf_arena_defaults_to_ffa_with_4_players(self, map_name):
        """Maps sharing CTF arena IDs default to FFA with 4 players."""
        room = resolve(map_name, players=4)
        assert room["game_mode"] == "ffa"


class TestUnverifiedCTFMaps:
    """Test that unverified CTF maps are rejected when explicitly requested."""

    @pytest.mark.parametrize("map_name", [
        "battle box",
        "skate city",
        "toxic tower",
        "space station",
        "space stations",
        "sky arena showdown",
        "snowpark ctf",
    ])
    def test_unverified_ctf_map_rejected_when_explicit(self, map_name):
        """Explicit CTF request on unverified allowlist map is rejected."""
        with pytest.raises(RoomIntentError, match="not yet verified"):
            resolve(map_name, mode="ctf", players=6)


class TestNonCTFMaps:
    """Test that non-CTF maps force FFA mode."""

    @pytest.mark.parametrize("map_name", [
        "gravel pit",
        "dropzone",
        "pinball",
        "showdown",
        "snowpark",
        "snow shrine",
        "steky's speedway",
        "slick n slide",
        "boneyard basin",
        "toxic towers",
        "sandbox",
        "the old graveyard",
        "graveyard ffa",
    ])
    def test_non_ctf_map_forces_ffa(self, map_name):
        """Non-CTF maps default to FFA even with 6+ players."""
        room = resolve(map_name, players=6)
        assert room["game_mode"] == "ffa"

    def test_gravel_pit_six_players_defaults_to_ffa(self):
        """Gravel Pit + 6 players defaults to FFA (explicit example from requirements)."""
        room = resolve("gravel pit", players=6)
        assert room["game_mode"] == "ffa"
        assert room["max_players"] == 6
        assert room["teams"] is True  # FFA teams ON by default


class TestCTFCompatibleMaps:
    """Test that CTF-verified maps follow normal default rules."""

    @pytest.mark.parametrize("map_name", [
        "smash fort ctf",
        "smash island ctf",
        "graveyard ctf",
        "lava pit ctf",
        "sky arena temples ctf",
        "sky arena tunnels ctf",
        # Maps sharing arena IDs with verified CTF maps:
        "smash fort",
        "smash island",
        "graveyard",
        "the graveyard",
        "lava pit",
        "temples",
        "tunnels",
    ])
    def test_ctf_compatible_map_with_four_players_defaults_ffa(self, map_name):
        """CTF-compatible map with 4 players defaults to FFA."""
        room = resolve(map_name, players=4)
        assert room["game_mode"] == "ffa"

    @pytest.mark.parametrize("map_name", [
        "smash fort ctf",
        "smash island ctf",
        "graveyard ctf",
        "lava pit ctf",
        "sky arena temples ctf",
        "sky arena tunnels ctf",
        # Maps sharing arena IDs with verified CTF maps:
        "smash fort",
        "smash island",
        "graveyard",
        "the graveyard",
        "lava pit",
        "temples",
        "tunnels",
    ])
    def test_ctf_compatible_map_with_six_players_defaults_ctf(self, map_name):
        """CTF-compatible map with 6 players defaults to CTF."""
        room = resolve(map_name, players=6)
        assert room["game_mode"] == "ctf"

    def test_explicit_ctf_on_verified_map_works(self):
        """Explicit CTF mode on verified map succeeds."""
        room = resolve("smash fort ctf", mode="ctf", players=4)
        assert room["game_mode"] == "ctf"


class TestShortformAliases:
    """Test that shortforms work correctly for CTF maps."""

    def test_sfc_shortform_for_ctf(self):
        """sfc shortform works for CTF."""
        room = resolve("sfc", mode="ctf", players=6)
        assert room["game_mode"] == "ctf"

    def test_sf_shortform_for_ffa(self):
        """sf shortform works for FFA (smash fort, not CTF)."""
        room = resolve("sf", players=4)
        assert room["game_mode"] == "ffa"

    def test_explicit_ctf_on_sf_shortform_uses_same_arena(self):
        """sf with explicit CTF uses same arena ID as sfc."""
        room = resolve("sf", mode="ctf", players=4)
        assert room["game_mode"] == "ctf"
        # Both sf and sfc map to smashfort arena ID
        from room_creator import normalize_arena
        assert normalize_arena(room["arena"]) == "smashfort"


class TestRandomMapSelection:
    """Test that random map selection respects mode."""

    def test_random_ctf_uses_only_verified_maps(self):
        """Random CTF selection only uses verified CTF maps."""
        for _ in range(20):  # Run multiple times to catch randomness
            room = resolve(None, mode="ctf", players=6)
            assert room["game_mode"] == "ctf"
            assert room["arena"] in CTF_VERIFIED_ARENA_IDS

    def test_random_ffa_uses_ffa_maps(self):
        """Random FFA selection only uses FFA-compatible maps."""
        for _ in range(20):
            room = resolve(None, mode="ffa", players=4)
            assert room["game_mode"] == "ffa"
            assert room["arena"] not in FFA_EXCLUDED_MAPS

    def test_random_no_mode_defaults_to_ctf_with_verified_map(self):
        """No mode + no map + 6 players → CTF with verified random map."""
        room = resolve(None, players=6)
        assert room["game_mode"] == "ctf"
        assert room["arena"] in CTF_VERIFIED_ARENA_IDS

    def test_random_no_mode_defaults_to_ffa_with_four_players(self):
        """No mode + no map + 4 players → FFA with random FFA map."""
        room = resolve(None, players=4)
        assert room["game_mode"] == "ffa"
        assert room["arena"] not in FFA_EXCLUDED_MAPS


class TestExplicitModeWithIncompatibleMap:
    """Test explicit mode requests with incompatible maps."""

    def test_explicit_ctf_on_non_ctf_map_rejected(self):
        """Explicit CTF on gravel pit is rejected."""
        with pytest.raises(RoomIntentError, match="does not support CTF"):
            resolve("gravel pit", mode="ctf", players=6)

    def test_explicit_ctf_on_dropzone_rejected(self):
        """Explicit CTF on dropzone is rejected."""
        with pytest.raises(RoomIntentError, match="does not support CTF"):
            resolve("dropzone", mode="ctf", players=8)

    def test_explicit_ffa_on_ctf_verified_map_works(self):
        """Explicit FFA on CTF-verified map works."""
        room = resolve("smash fort ctf", mode="ffa", players=4)
        assert room["game_mode"] == "ffa"

    def test_explicit_ctf_on_unverified_allowlist_map_rejected(self):
        """Explicit CTF on unverified allowlist map (e.g., battle box) rejected."""
        with pytest.raises(RoomIntentError, match="not yet verified"):
            resolve("battle box", mode="ctf", players=6)


class TestArenaIDVerification:
    """Test that arena IDs are properly verified and not fabricated."""

    def test_ctf_verified_arena_ids_match_verified_maps(self):
        """CTF_VERIFIED_ARENA_IDS contains exactly the arena IDs from verified maps."""
        expected = set(CTF_VERIFIED_MAPS.values())
        assert CTF_VERIFIED_ARENA_IDS == expected

    def test_no_fabricated_arena_ids_for_unverified_maps(self):
        """Maps without verified CTF mappings don't have fabricated CTF arena IDs."""
        # Battle box has an arena ID for FFA, but not a verified CTF arena ID
        assert "battle box" in ARENA_IDS
        assert "battlebox" not in CTF_VERIFIED_ARENA_IDS

    def test_unknown_arena_raises_error(self):
        """Unknown arena names raise ValueError (caught as RoomIntentError)."""
        with pytest.raises(RoomIntentError, match="Unknown/untested arena"):
            resolve("made up map", players=6)

    def test_arena_ids_are_unique_for_verified_ctf(self):
        """Each verified CTF map has a valid arena ID."""
        for map_name, arena_id in CTF_VERIFIED_MAPS.items():
            assert arena_id in ARENA_IDS.values()
            # Verify the arena ID normalizes correctly
            assert normalize_arena(map_name) == arena_id


class TestLanguageResponses:
    """Test that responses match user's language."""

    @pytest.mark.parametrize("text,expected_lang", [
        ("Hey Buddy, how are you?", "english"),
        ("Hello world", "english"),
        ("Thanks!", "english"),
        ("Bhai kya haal hai?", "hinglish"),
        ("Room bnao please", "hinglish"),
        ("Bhai khelna hai", "hinglish"),
        ("भाई रूम बना दो", "hindi"),
        ("रूम बना दो", "hindi"),
    ])
    def test_language_detection(self, text, expected_lang):
        """Language detection works for various inputs."""
        from bot import detect_language, Language
        assert detect_language(text) == Language(expected_lang)


class TestShortEnglishNotHinglish:
    """Test that short English phrases aren't misclassified as Hinglish."""

    @pytest.mark.parametrize("text", [
        "hi",
        "hey",
        "hello",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "yes",
        "no",
        "yo",
        "sup",
        "gm",
        "gn",
        "bye",
        "good morning",
        "good night",
        "how are you",
        "what's up",
    ])
    def test_short_english_phrases_are_english(self, text):
        """Common English greetings/phrases are detected as English."""
        from bot import detect_language, Language
        assert detect_language(text) == Language.ENGLISH, f"'{text}' was not detected as English"


class TestExistingFunctionalityPreserved:
    """Test that existing functionality still works."""

    def test_ffa_teams_on_by_default(self):
        """FFA teams are ON by default."""
        room = resolve("gravel pit", players=4)
        assert room["teams"] is True

    def test_ctf_teams_always_off(self):
        """CTF teams are always OFF."""
        room = resolve("smash fort ctf", mode="ctf", players=8, teams=True)
        assert room["teams"] is False

    def test_default_timer_10_minutes(self):
        """Default timer is 10 minutes."""
        room = resolve("gravel pit", players=6)
        assert room["round_format"] == "timer"
        assert room["value"] == 10

    def test_default_players_6(self):
        """Default player count is 6."""
        room = resolve("gravel pit")
        assert room["max_players"] == 6

    def test_default_disabled_weapons(self):
        """Default disabled weapons are machine_gun, invincibility, lob_sentry."""
        room = resolve("gravel pit")
        from room_creator import DEFAULT_WEAPONS, WEAPON_BITS
        expected = DEFAULT_WEAPONS
        for w in ("machine_gun", "invincibility", "lob_sentry"):
            expected &= ~WEAPON_BITS[w]
        assert room["weapons"] == expected

    def test_url_encoding_unchanged(self):
        """URL encoding logic is unchanged."""
        from room_creator import create_room_url
        url = create_room_url(
            game_mode="ffa",
            round_format="timer",
            value=10,
            arena="gravel pit",
            max_players=6,
            teams=True,
        )
        assert "smashkarts.io/link/" in url
        assert "mode=" in url
        assert "wpns=" in url
        assert "room=in3" in url
        assert "arena=gravelpit" in url
        assert "mdfrs=0" in url


if __name__ == "__main__":
    pytest.main([__file__, "-v"])