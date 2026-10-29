import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "api"))

import bot
import discord
from api.room_intent import RoomIntent


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeChannel:
    def __init__(self, channel_id=10, referenced=None):
        self.id = channel_id
        self.referenced = referenced
        self.sent = []

    def typing(self):
        return FakeTyping()

    async def send(self, content, **kwargs):
        self.sent.append(content)

    async def fetch_message(self, message_id):
        if isinstance(self.referenced, Exception):
            raise self.referenced
        return self.referenced


class FakeAuthor:
    def __init__(self, user_id=20, is_bot=False):
        self.id = user_id
        self.bot = is_bot


class FakeMessage:
    def __init__(self, content, channel, reference=None, author=None):
        self.content = content
        self.channel = channel
        self.reference = reference
        self.author = author or FakeAuthor()
        self.replies = []

    async def reply(self, content, **kwargs):
        self.replies.append(content)


def buddy_with_user():
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
    return buddy


def test_room_only_channel_ignores_normal_message(monkeypatch):
    buddy = buddy_with_user()
    monkeypatch.setattr(bot, "ROOM_REQUEST_CHANNEL_IDS", frozenset({10}))
    called = []

    async def fake_handle(*args, **kwargs):
        called.append(True)

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)
    asyncio.run(buddy.on_message(FakeMessage("bhai kya haal hai", FakeChannel())))
    assert called == []


def test_room_only_channel_creates_room_without_chat_fallback(monkeypatch):
    buddy = buddy_with_user()
    monkeypatch.setattr(bot, "ROOM_REQUEST_CHANNEL_IDS", frozenset({10}))
    calls = []

    async def fake_handle(message, prompt, **kwargs):
        calls.append((prompt, kwargs))

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)
    asyncio.run(buddy.on_message(FakeMessage("bhai room bnade", FakeChannel())))
    assert calls == [("bhai room bnade", {"allow_chat": False, "allow_room": True, "reply_context": None})]


def test_chat_channel_allows_chat_and_room(monkeypatch):
    buddy = buddy_with_user()
    monkeypatch.setattr(bot, "CHAT_CHANNEL_IDS", frozenset({10}))
    calls = []

    async def fake_handle(message, prompt, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)
    asyncio.run(buddy.on_message(FakeMessage("bhai kya haal hai", FakeChannel())))
    assert calls == [{"allow_chat": True, "allow_room": True, "reply_context": None}]


def test_other_channel_requires_mention(monkeypatch):
    buddy = buddy_with_user()
    calls = []

    async def fake_handle(*args, **kwargs):
        calls.append(True)

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)
    asyncio.run(buddy.on_message(FakeMessage("bhai room bnade", FakeChannel())))
    assert calls == []

    asyncio.run(
        buddy.on_message(FakeMessage("<@123> bhai room bnade", FakeChannel()))
    )
    assert calls == [True]


def test_cached_buddy_reply_is_addressed(monkeypatch):
    buddy = buddy_with_user()
    referenced = FakeMessage("old Buddy reply", FakeChannel(), author=FakeAuthor(123, True))
    reference = type("Reference", (), {"resolved": referenced, "message_id": 1})()
    calls = []

    async def fake_handle(message, prompt, **kwargs):
        calls.append(kwargs["reply_context"])

    monkeypatch.setattr(buddy, "handle_prompt", fake_handle)
    message = FakeMessage("continue", FakeChannel(), reference=reference)
    asyncio.run(buddy.on_message(message))
    assert calls == ["old Buddy reply"]


def test_uncached_buddy_reply_fetches_and_human_reply_does_not():
    buddy = buddy_with_user()
    referenced = FakeMessage("Buddy reply", FakeChannel(), author=FakeAuthor(123, True))
    reference = type("Reference", (), {"resolved": None, "message_id": 1})()
    calls = []

    async def fake_handle(message, prompt, **kwargs):
        calls.append(prompt)

    buddy.handle_prompt = fake_handle
    asyncio.run(
        buddy.on_message(
            FakeMessage("continue", FakeChannel(referenced=referenced), reference)
        )
    )
    assert calls == ["continue"]

    human = FakeMessage("human reply", FakeChannel(), author=FakeAuthor(999))
    asyncio.run(
        buddy.on_message(
            FakeMessage("continue", FakeChannel(referenced=human), reference)
        )
    )
    assert calls == ["continue"]


def test_deleted_reply_reference_is_ignored():
    buddy = buddy_with_user()
    reference = type("Reference", (), {"resolved": None, "message_id": 1})()
    response = type("Response", (), {"status": 404, "reason": "Not Found"})()
    missing = discord.NotFound(response, "deleted")
    message = FakeMessage("continue", FakeChannel(missing), reference)
    asyncio.run(buddy.on_message(message))


def test_normal_chat_skips_room_extraction(monkeypatch):
    buddy = buddy_with_user()
    room_calls = []
    chat_calls = []

    async def unexpected_room(prompt):
        room_calls.append(prompt)
        return None

    async def chat(prompt, context=None):
        chat_calls.append(prompt)
        return "mast bhai"

    monkeypatch.setattr(buddy, "create_room_reply", unexpected_room)
    monkeypatch.setattr(buddy, "ask_gemini", chat)
    message = FakeMessage("python decorator explain", FakeChannel())
    asyncio.run(
        buddy.handle_prompt(message, message.content, allow_chat=True, allow_room=True)
    )
    assert room_calls == []
    assert message.replies == ["mast bhai"]


def test_casual_ctf_request_is_a_room_candidate():
    assert bot.is_room_request_candidate("bhai ctf khelna hai lava pit me")
    assert not bot.is_room_request_candidate("room ka system kaise kaam karta hai")


def test_gemini_rate_limit_returns_friendly_error():
    buddy = buddy_with_user()

    async def fail(*args):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    buddy._ask_gemini_async = fail
    assert asyncio.run(buddy.ask_gemini("hello")) == bot.GEMINI_FAILURE_REPLY


def test_cooldown_prevents_duplicate_responses(monkeypatch):
    buddy = buddy_with_user()
    calls = []

    async def fake_chat(prompt, context=None):
        calls.append(prompt)
        return "reply"

    async def no_room(prompt):
        return None

    monkeypatch.setattr(buddy, "ask_gemini", fake_chat)
    monkeypatch.setattr(buddy, "create_room_reply", no_room)
    message = FakeMessage("hello", FakeChannel())
    asyncio.run(buddy.handle_prompt(message, "hello"))
    asyncio.run(buddy.handle_prompt(message, "hello"))
    assert calls == ["hello"]


def test_room_creation_uses_one_structured_request(monkeypatch):
    buddy = buddy_with_user()
    calls = []

    class Models:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            return type(
                "Response",
                (),
                {
                    "parsed": RoomIntent(is_room_request=True, map="sf", players=4),
                },
            )()

    buddy.gemini_client = type("Client", (), {"aio": type("Aio", (), {"models": Models()})()})()
    reply = asyncio.run(buddy.create_room_reply("sf me 4 ka room bana"))
    assert reply.count("\n") == 1
    assert len(calls) == 1
    assert calls[0]["model"] == bot.GEMINI_MODEL


def test_long_chat_response_is_split_without_truncation(monkeypatch):
    buddy = buddy_with_user()
    text = "word " * 900
    monkeypatch.setattr(buddy, "ask_gemini", lambda *args: asyncio.sleep(0, result=text))
    message = FakeMessage("explain this", FakeChannel())
    asyncio.run(buddy.handle_prompt(message, message.content))
    assert len(message.replies) == 1
    assert len(message.replies[0]) <= 2000
    combined = "".join(message.replies + message.channel.sent)
    assert combined.replace(" ", "") == text.strip().replace(" ", "")


def test_deferred_ask_response_is_split_into_followups():
    class Followup:
        def __init__(self):
            self.messages = []

        async def send(self, content, **kwargs):
            self.messages.append(content)

    class Interaction:
        def __init__(self):
            self.followup = Followup()

    interaction = Interaction()
    text = "word " * 900
    asyncio.run(bot.send_interaction_text(interaction, text))
    assert len(interaction.followup.messages) > 1
    assert all(len(message) <= 2000 for message in interaction.followup.messages)
    assert "".join(interaction.followup.messages).replace(" ", "") == text.strip().replace(" ", "")
