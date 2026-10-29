import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "api"))

import bot
import discord
from room_intent import RoomIntent


def test_unrelated_message_is_left_for_regular_gemini(monkeypatch):
    buddy = bot.BuddyBot(gemini_client=object())
    monkeypatch.setattr(
        buddy,
        "extract_room_intent",
        lambda prompt: RoomIntent(is_room_request=False),
    )
    assert asyncio.run(buddy.create_room_reply("kya haal hai")) is None


def test_successful_room_sends_one_sentence_then_one_url(monkeypatch):
    buddy = bot.BuddyBot(gemini_client=object())
    monkeypatch.setattr(
        buddy,
        "extract_room_intent",
        lambda prompt: RoomIntent(is_room_request=True, map="sf"),
    )
    monkeypatch.setattr(
        bot,
        "create_room_from_intent",
        lambda intent: "https://smashkarts.io/link/?room=in312345",
    )
    monkeypatch.setattr(bot.random, "choice", lambda options: options[0])

    reply = asyncio.run(buddy.create_room_reply("bhai room bnade"))
    lines = reply.splitlines()
    assert len(lines) == 2
    assert lines[0]
    assert "http" not in lines[0]
    assert lines[1].startswith("https://smashkarts.io/link/")


def test_discord_message_handler_replies_to_mention(monkeypatch):
    buddy = bot.BuddyBot(gemini_client=object())
    buddy._connection.user = discord.ClientUser(
        state=buddy._connection,
        data={
            "id": 123,
            "username": "Buddy",
            "discriminator": "0001",
            "avatar": None,
        },
    )
    calls = []

    async def fake_handle(message, prompt):
        calls.append((message, prompt))

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)
    async def skip_commands(message):
        return None

    monkeypatch.setattr(buddy, "process_commands", skip_commands)

    class Author:
        bot = False

    class Message:
        author = Author()
        content = "<@123> bhai room bnade"
        reference = None

    asyncio.run(buddy.on_message(Message()))
    assert len(calls) == 1
    assert calls[0][1] == "bhai room bnade"
