"""Buddy Discord bot entry point."""

from __future__ import annotations

import os
import random
import asyncio
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
BOT_PREFIX = os.getenv("BOT_PREFIX", "/")
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))
GEMINI_MAX_CONCURRENCY = int(os.getenv("GEMINI_MAX_CONCURRENCY", "4"))
MESSAGE_COOLDOWN_SECONDS = float(os.getenv("MESSAGE_COOLDOWN_SECONDS", "3"))


def _channel_ids(name: str) -> frozenset[int]:
    values = os.getenv(name, "")
    return frozenset(int(value.strip()) for value in values.split(",") if value.strip())


AUTO_CHAT_CHANNEL_IDS = _channel_ids("AUTO_CHAT_CHANNEL_IDS")
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
    "Saale har baar mujhe hi room banana padta hai🤧 ",
)


def extract_response_text(response) -> str | None:
    text = getattr(response, "text", None)
    if text:
        return text.strip() or None
    return None


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
            self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        return self.gemini_client

    async def _run_gemini(self, operation: Callable, *args):
        """Run the synchronous Gemini SDK without blocking Discord's loop."""
        try:
            await asyncio.wait_for(
                self._gemini_slots.acquire(),
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Buddy is busy, try again in a moment 💀") from exc

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(operation, *args),
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
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

    def extract_room_intent(self, prompt: str) -> RoomIntent | None:
        response = self._gemini().models.generate_content(
            model="gemini-3.1-flash-lite",
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
            return None
        if intent is None or not intent.is_room_request:
            return None
        try:
            url = create_room_from_intent(intent)
            map_name = intent.map or "random map"
            sentence = random.choice(ROOM_RESPONSES).format(map_name=map_name)
            return f"{sentence}\n{url}"
        except RoomIntentError as exc:
            return str(exc)

    def _ask_gemini_sync(self, prompt: str, reply_context: str | None = None) -> str:
        context = prompt
        if reply_context:
            context = f"Message being replied to:\n{reply_context}\n\nUser:\n{prompt}"
        response = self._gemini().models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                max_output_tokens=500,
                temperature=0.9,
            ),
        )
        return extract_response_text(response) or "Brain.exe crashed 💀"

    async def ask_gemini(self, prompt: str, reply_context: str | None = None) -> str:
        return await self._run_gemini(self._ask_gemini_sync, prompt, reply_context)

    async def handle_prompt(self, message: discord.Message, prompt: str):
        if self._on_cooldown(message):
            return
        try:
            room_reply = await self.create_room_reply(prompt)
            reply = room_reply or await self.ask_gemini(
                prompt,
                getattr(getattr(message, "reference", None), "resolved", None)
                and getattr(message.reference.resolved, "content", None),
            )
        except Exception as exc:
            print("Buddy request error:", repr(exc))
            reply = "Brain.exe crashed 💀"
        await message.reply(reply, mention_author=False)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        prompt = message.content
        mentioned = self.user is not None and (
            self.user.mention in prompt
            or f"<@!{self.user.id}>" in prompt
        )
        replied_to_buddy = (
            getattr(message.reference, "resolved", None) is not None
            and getattr(
                getattr(message.reference.resolved, "author", None),
                "id",
                None,
            )
            == getattr(self.user, "id", None)
        )
        auto_chat = message.channel.id in AUTO_CHAT_CHANNEL_IDS
        room_channel = message.channel.id in ROOM_REQUEST_CHANNEL_IDS
        if not mentioned and not replied_to_buddy and not auto_chat and not room_channel:
            return
        if self.user:
            prompt = prompt.replace(self.user.mention, "").strip()
            prompt = prompt.replace(f"<@!{self.user.id}>", "").strip()
        if prompt:
            await self.handle_prompt(message, prompt)


bot = BuddyBot()


@bot.tree.command(name="ask", description="Ask Buddy anything")
async def ask_command(interaction: discord.Interaction, question: str):
    try:
        await interaction.response.defer()
        reply = await bot.ask_gemini(question)
    except Exception as exc:
        print("Slash ask error:", repr(exc))
        reply = "Brain.exe crashed 💀"
    if interaction.response.is_done():
        await interaction.followup.send(reply)
    else:
        await interaction.response.send_message(reply)


@bot.tree.command(name="room", description="Create a Smash Karts room")
async def room_command(interaction: discord.Interaction, request: str):
    try:
        await interaction.response.defer()
        reply = await bot.create_room_reply(request)
        reply = reply or "Abe room ka request toh de, mind-reader nahi hoon 💀"
    except Exception as exc:
        print("Slash room error:", repr(exc))
        reply = "Room nahi bana, game ne bhi mujhe reject kar diya 💀"
    if interaction.response.is_done():
        await interaction.followup.send(reply)
    else:
        await interaction.response.send_message(reply)


def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not configured.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
