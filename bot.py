"""Buddy Discord bot entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import inspect
import os
import random
import re
import time
from collections.abc import Callable

import discord
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

from api.room_intent import RoomIntent, RoomIntentError, create_room_from_intent

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
BOT_PREFIX = os.getenv("BOT_PREFIX", "/")
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))
GEMINI_MAX_CONCURRENCY = int(os.getenv("GEMINI_MAX_CONCURRENCY", "4"))
MESSAGE_COOLDOWN_SECONDS = float(os.getenv("MESSAGE_COOLDOWN_SECONDS", "3"))


def _channel_ids(name: str) -> frozenset[int]:
    values = os.getenv(name, "")
    return frozenset(int(value.strip()) for value in values.split(",") if value.strip())


CHAT_CHANNEL_IDS = _channel_ids("CHAT_CHANNEL_IDS")
ROOM_REQUEST_CHANNEL_IDS = _channel_ids("ROOM_REQUEST_CHANNEL_IDS")

SYSTEM_INSTRUCTION = """
You are Buddy, a bakchod Indian gaming friend in a Discord server.
Sound like a real friend: funny, sarcastic, mischievous, informal, and
comfortable with English, Hindi, and natural Hinglish. Match the user's tone.
Use playful roasting and occasional friendly words like bc, bkl, saale, or
chutiye only when they fit; never force profanity, hateful slurs, threats, or
genuinely hostile harassment. Keep casual replies short and spontaneous, but
give accurate, useful answers to serious technical or general questions.
Do not sound like customer support, use canned greetings, or mention being an
AI unless asked. Do not generate room URLs; room creation is handled by Python.
"""

ROOM_INTENT_INSTRUCTION = """
Extract a Smash Karts room request from the user's current message.
Return only explicitly stated settings; use null for unspecified settings.
Understand English, Hindi, Hinglish, slang, typos such as bnado, bna de,
and invincival. A request to make/create/bana a room sets is_room_request
to true. Unrelated chat sets it to false.
Recognize all supported map aliases, FFA/CTF, player counts, timer minutes,
score targets, team on/off, and explicitly enabled weapon names.
Never invent defaults, URLs, maps, settings, or weapons.
"""

ROOM_RESPONSES = (
    "Le bkl {map_name} khel tu 😂",
    "Le bc room bana diya, ab haarne mat lag jaana 💀",
    "Abe noob, ye le tera {map_name} room.",
    "Room ready hai chutiye, ab kuch kills bhi kar lena 😂",
    "Le bhai, room bana diya. Ab rona mat haar ke.",
    "Saale har baar mujhe hi room banana padta hai 🤧",
)

ROOM_FAILURE_REPLY = "Abe bc Gemini ka dimaag hang ho gaya, thodi der baad try kar 💀"
GEMINI_FAILURE_REPLY = "Abe bc Gemini ka dimaag hang ho gaya, thodi der baad try kar 💀"
DISCORD_MESSAGE_LIMIT = 2000


def extract_response_text(response) -> str | None:
    text = getattr(response, "text", None)
    if text:
        return text.strip() or None
    return None


def is_room_request_candidate(text: str) -> bool:
    """Cheap gate that avoids an intent API call for ordinary conversation."""
    normalized = " ".join(text.casefold().split())
    if re.search(r"\b(?:banaya|banaaya|made|created)\s+tha\b", normalized):
        return False
    if re.search(r"\b(?:room|match)\b.*\b(?:kaise|how|system|meaning)\b", normalized):
        return False

    room_nouns = r"(?:room|match|game)"
    action_words = (
        r"(?:bana(?:\s+de)?|bnade|bnado|bna\s+de|create|make|khol(?:\s+de)?|"
        r"open|start|chahiye|karo|kar\s+do|de\s+de)"
    )
    if re.search(rf"\b{room_nouns}\b.*\b{action_words}\b", normalized):
        return True
    if re.search(rf"\b{action_words}\b.*\b{room_nouns}\b", normalized):
        return True
    if re.search(r"\b(?:random|ctf|ffa)\s+(?:map\s+pe\s+)?(?:room|match)\b", normalized):
        return True
    if re.search(
        r"\b(?:ctf|ffa)\b.*\b(?:khel(?:na)?|play)\b.*\b"
        r"(?:sf|si|gp|dz|lp|dropzone|lava\s+pit|smash\s+fort|smash\s+island)\b",
        normalized,
    ):
        return True
    if re.search(r"\b\d+\s*(?:player|players|bande|bandon|logo|logon)\b.*\broom\b", normalized):
        return True
    if re.search(r"\b(?:sf|si|gp|dz|lp|pp|sd|te|tu|t|ss|gy|sns)\b.*\broom\b", normalized):
        return True
    return False


def split_discord_text(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split long output at whitespace without silently truncating it."""
    chunks = []
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks or [""]


@asynccontextmanager
async def _noop_typing():
    yield


async def send_interaction_text(interaction: discord.Interaction, text: str):
    """Send a deferred interaction response in Discord-sized chunks."""
    for chunk in split_discord_text(text):
        await interaction.followup.send(
            chunk,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class BuddyBot(commands.Bot):
    def __init__(self, *, gemini_client=None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=BOT_PREFIX, intents=intents)
        self.gemini_client = gemini_client
        self._gemini_slots = asyncio.Semaphore(GEMINI_MAX_CONCURRENCY)
        self._cooldowns: dict[tuple[int, int], float] = {}

    async def setup_hook(self):
        await self.tree.sync()

    def _gemini(self):
        if self.gemini_client is None:
            if not GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY is not configured.")
            self.gemini_client = genai.Client(
                api_key=GEMINI_API_KEY,
                http_options=types.HttpOptions(
                    timeout=int(GEMINI_TIMEOUT_SECONDS * 1000),
                ),
            )
        return self.gemini_client

    async def _run_gemini(self, operation: Callable, *args):
        """Bound and time an SDK request; cancellation depends on SDK support."""
        try:
            await asyncio.wait_for(
                self._gemini_slots.acquire(),
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Buddy is busy, try again in a moment 💀") from exc

        try:
            result = operation(*args)
            if inspect.isawaitable(result):
                return await asyncio.wait_for(result, timeout=GEMINI_TIMEOUT_SECONDS)
            return result
        finally:
            self._gemini_slots.release()

    def _on_cooldown(self, message: discord.Message) -> bool:
        now = time.monotonic()
        key = (message.channel.id, message.author.id)
        previous = self._cooldowns.get(key, 0)
        if now - previous < MESSAGE_COOLDOWN_SECONDS:
            return True
        self._cooldowns[key] = now
        return False

    async def extract_room_intent(self, prompt: str) -> RoomIntent | None:
        response = await self._gemini().aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=ROOM_INTENT_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=RoomIntent,
                max_output_tokens=220,
                temperature=0,
            ),
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, RoomIntent):
            return parsed
        if isinstance(parsed, dict):
            return RoomIntent.model_validate(parsed)
        text = extract_response_text(response)
        return RoomIntent.model_validate_json(text) if text else None

    async def create_room_reply(self, prompt: str) -> str | None:
        try:
            intent = await self._run_gemini(self.extract_room_intent, prompt)
        except Exception as exc:
            print("Room intent error:", repr(exc))
            return ROOM_FAILURE_REPLY
        if intent is None or not intent.is_room_request:
            return None
        try:
            url = create_room_from_intent(intent)
            map_name = intent.map or "random map"
            sentence = random.choice(ROOM_RESPONSES).format(map_name=map_name)
            return f"{sentence}\n{url}"
        except RoomIntentError as exc:
            return str(exc)

    async def _ask_gemini_async(
        self,
        prompt: str,
        reply_context: str | None = None,
    ) -> str:
        context = prompt
        if reply_context:
            context = f"Message being replied to:\n{reply_context}\n\nUser:\n{prompt}"
        response = await self._gemini().aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                max_output_tokens=500,
                temperature=0.9,
            ),
        )
        return extract_response_text(response) or GEMINI_FAILURE_REPLY

    async def ask_gemini(self, prompt: str, reply_context: str | None = None) -> str:
        # Kept as a test seam for existing mocked tests; production uses the async SDK.
        test_operation = self.__dict__.get("_ask_gemini_sync")
        operation = test_operation or self._ask_gemini_async
        try:
            return await self._run_gemini(operation, prompt, reply_context)
        except Exception as exc:
            print("Gemini request error:", repr(exc))
            return GEMINI_FAILURE_REPLY

    async def _referenced_message(self, message: discord.Message):
        reference = getattr(message, "reference", None)
        if reference is None:
            return None
        resolved = getattr(reference, "resolved", None)
        if resolved is not None:
            return resolved
        message_id = getattr(reference, "message_id", None)
        if message_id is None:
            return None
        try:
            return await message.channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print("Reply reference unavailable:", repr(exc))
            return None

    async def _reply_is_to_buddy(self, message: discord.Message) -> tuple[bool, str | None]:
        referenced = await self._referenced_message(message)
        if referenced is None or self.user is None:
            return False, None
        author = getattr(referenced, "author", None)
        return (
            getattr(author, "id", None) == self.user.id,
            getattr(referenced, "content", None),
        )

    async def _send_reply(self, message: discord.Message, text: str):
        chunks = split_discord_text(text)
        for index, chunk in enumerate(chunks):
            if index == 0:
                await message.reply(
                    chunk,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await message.channel.send(
                    chunk,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

    async def handle_prompt(
        self,
        message: discord.Message,
        prompt: str,
        *,
        allow_chat: bool = True,
        allow_room: bool = True,
        reply_context: str | None = None,
    ):
        if self._on_cooldown(message):
            return
        typing = getattr(message.channel, "typing", None)
        async with (typing() if typing else _noop_typing()):
            try:
                if allow_room and is_room_request_candidate(prompt):
                    room_reply = await self.create_room_reply(prompt)
                    if room_reply is not None:
                        await self._send_reply(message, room_reply)
                        return
                    if not allow_chat:
                        return
                if allow_chat:
                    reply = await self.ask_gemini(prompt, reply_context)
                    await self._send_reply(message, reply)
            except Exception as exc:
                print("Buddy request error:", repr(exc))
                await self._send_reply(message, GEMINI_FAILURE_REPLY)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        prompt = message.content or ""
        mentioned = False
        if self.user is not None:
            mentioned = self.user.mention in prompt or f"<@!{self.user.id}>" in prompt
        replied_to_buddy, reply_context = await self._reply_is_to_buddy(message)
        addressed = mentioned or replied_to_buddy
        channel_id = message.channel.id
        configured_chat = channel_id in CHAT_CHANNEL_IDS
        room_channel = channel_id in ROOM_REQUEST_CHANNEL_IDS
        allow_chat = addressed or configured_chat
        allow_room = addressed or room_channel or configured_chat
        room_candidate = is_room_request_candidate(prompt)

        if not allow_chat and not (allow_room and room_candidate):
            return
        if self.user is not None:
            prompt = prompt.replace(self.user.mention, "")
            prompt = prompt.replace(f"<@!{self.user.id}>", "").strip()
        if not prompt:
            prompt = "Respond to the message being replied to." if reply_context else ""
        if prompt:
            await self.handle_prompt(
                message,
                prompt,
                allow_chat=allow_chat,
                allow_room=allow_room,
                reply_context=reply_context,
            )


bot = BuddyBot()


@bot.tree.command(name="ask", description="Ask Buddy anything")
async def ask_command(interaction: discord.Interaction, question: str):
    try:
        await interaction.response.defer()
        reply = await bot.ask_gemini(question)
        await send_interaction_text(interaction, reply)
    except Exception as exc:
        print("Slash ask error:", repr(exc))
        if interaction.response.is_done():
            await interaction.followup.send(GEMINI_FAILURE_REPLY)
        else:
            await interaction.response.send_message(GEMINI_FAILURE_REPLY)


@bot.tree.command(name="room", description="Create a Smash Karts room")
async def room_command(interaction: discord.Interaction, request: str):
    try:
        await interaction.response.defer()
        reply = await bot.create_room_reply(request)
        reply = reply or "Abe room ka request toh de, mind-reader nahi hoon 💀"
        await interaction.followup.send(
            reply,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        print("Slash room error:", repr(exc))
        if interaction.response.is_done():
            await interaction.followup.send(ROOM_FAILURE_REPLY)
        else:
            await interaction.response.send_message(ROOM_FAILURE_REPLY)


def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not configured.")
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
