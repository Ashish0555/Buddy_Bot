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

    async def fake_handle(message, prompt, **kwargs):
        calls.append((message, prompt))

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)
    async def skip_commands(message):
        return None

    monkeypatch.setattr(buddy, "process_commands", skip_commands)

    class Author:
        bot = False
        id = 456

    class Channel:
        id = 789

    class Message:
        author = Author()
        channel = Channel()
        content = "<@123> bhai room bnade"
        reference = None

    asyncio.run(buddy.on_message(Message()))
    assert len(calls) == 1
    assert calls[0][1] == "bhai room bnade"


def test_chat_response_uses_gemini_and_reply_context():
    buddy = bot.BuddyBot(gemini_client=object())
    calls = []

    def fake_sync(prompt, reply_context=None, language=None):
        calls.append((prompt, reply_context, language))
        return "Abe bkl, decorator samajh pehle 😂"

    buddy._ask_gemini_sync = fake_sync
    reply = asyncio.run(buddy.ask_gemini("decorator kya hota hai?", "previous answer"))
    assert reply.startswith("Abe bkl")
    assert calls == [("decorator kya hota hai?", "previous answer", bot.Language.HINGLISH)]


def test_chat_generation_uses_short_response_instruction_and_token_headroom():
    calls = []

    class Models:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"text": "Theek hai bhai 😂"})()

    client = type(
        "Client",
        (),
        {"aio": type("Aio", (), {"models": Models()})()},
    )()
    buddy = bot.BuddyBot(gemini_client=client)
    reply = asyncio.run(buddy._ask_gemini_async("hello"))

    assert reply == "Theek hai bhai 😂"
    config = calls[0]["config"]
    assert "5–20 words" in config.system_instruction
    assert "never exceed 25 words" in config.system_instruction
    assert config.max_output_tokens == 512


def test_unmentioned_message_is_ignored(monkeypatch):
    buddy = bot.BuddyBot(gemini_client=object())
    calls = []

    async def fake_handle(message, prompt, **kwargs):
        calls.append(prompt)

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)

    class Author:
        bot = False
        id = 456

    class Channel:
        id = 789

    class Message:
        author = Author()
        channel = Channel()
        content = "normal group chatter"
        reference = None

    asyncio.run(buddy.on_message(Message()))
    assert calls == []


def test_auto_chat_channel_routes_without_mention(monkeypatch):
    buddy = bot.BuddyBot(gemini_client=object())
    monkeypatch.setattr(bot, "CHAT_CHANNEL_IDS", frozenset({789}))
    calls = []

    async def fake_handle(message, prompt, **kwargs):
        calls.append(prompt)

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)

    class Author:
        bot = False
        id = 456

    class Channel:
        id = 789

    class Message:
        author = Author()
        channel = Channel()
        content = "bhai kya haal hai"
        reference = None

    asyncio.run(buddy.on_message(Message()))
    assert calls == ["bhai kya haal hai"]


def test_bot_messages_are_ignored():
    buddy = bot.BuddyBot(gemini_client=object())
    calls = []

    async def fake_handle(message, prompt):
        calls.append(prompt)

    buddy.handle_prompt = fake_handle

    class Author:
        bot = True
        id = 456

    class Channel:
        id = 789

    class Message:
        author = Author()
        channel = Channel()
        content = "<@123> hello"
        reference = None

    asyncio.run(buddy.on_message(Message()))
    assert calls == []


def test_gemini_failure_returns_friendly_error():
    buddy = bot.BuddyBot(gemini_client=object())

    def fail(*args):
        raise TimeoutError("timed out")

    buddy._ask_gemini_sync = fail
    async def no_room(prompt):
        return None

    buddy.create_room_reply = no_room
    replies = []

    class Author:
        bot = False
        id = 456

    class Channel:
        id = 789

    class Message:
        author = Author()
        channel = Channel()

        async def reply(self, text, mention_author=False, allowed_mentions=None):
            replies.append(text)

    asyncio.run(buddy.handle_prompt(Message(), "hello"))
    assert replies == [bot.GEMINI_FAILURE_REPLY]
