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
CHAT_MAX_WORDS = int(os.getenv("CHAT_MAX_WORDS", "25"))
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))
GEMINI_MAX_CONCURRENCY = int(os.getenv("GEMINI_MAX_CONCURRENCY", "4"))
MESSAGE_COOLDOWN_SECONDS = float(os.getenv("MESSAGE_COOLDOWN_SECONDS", "3"))


def _channel_ids(name: str) -> frozenset[int]:
    values = os.getenv(name, "")
    return frozenset(int(value.strip()) for value in values.split(",") if value.strip())


CHAT_CHANNEL_IDS = _channel_ids("CHAT_CHANNEL_IDS")
ROOM_REQUEST_CHANNEL_IDS = _channel_ids("ROOM_REQUEST_CHANNEL_IDS")

SYSTEM_INSTRUCTION = f"""
You are Buddy, a bakchod Indian gaming friend in a Discord server, who speaks English ,Hinglish,hindi, tamil , talugu.
always reply in user/query language.
Sound like a real friend: funny, sarcastic, mischievous, informal, and
comfortable with English, Hindi, and natural Hinglish. Match the user's tone.
Use playful roasting and occasional friendly words like bc, bkl, saale, or
chutiye only when they fit; never force profanity, hateful slurs, threats, or
genuinely hostile harassment. Keep casual replies short and spontaneous, but
usually use 5–20 words and never exceed {CHAT_MAX_WORDS} words for ordinary
conversation. Prefer one natural sentence, not multiple paragraphs. Skip
unnecessary explanations, greetings, and follow-up questions. For serious
questions or explicit requests for detail, give an accurate, useful answer
even if it needs to be longer than the ordinary chat limit.
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


from enum import StrEnum


class Language(StrEnum):
    """Detected language/style of user message."""
    ENGLISH = "english"
    HINDI = "hindi"
    HINGLISH = "hinglish"
    OTHER = "other"


def detect_language(text: str) -> Language:
    """Detect the dominant language/writing style of the input text.

    Returns:
        Language.ENGLISH: Primarily English (Latin script, English words)
        Language.HINDI: Primarily Hindi in Devanagari script
        Language.HINGLISH: Mix of Roman Hindi/Urdu and English
        Language.OTHER: Other languages/scripts
    """
    if not text or not text.strip():
        return Language.ENGLISH

    # Check for explicit language override instructions
    lower = text.lower()
    if re.search(r"\b(?:reply|respond|answer)\s+in\s+(english|hindi|hinglish)\b", lower):
        lang_map = {"english": Language.ENGLISH, "hindi": Language.HINDI, "hinglish": Language.HINGLISH}
        for k, v in lang_map.items():
            if k in lower:
                return v

    # Count characters by script
    devanagari = sum(1 for ch in text if "\u0900" <= ch <= "\u097F")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    other_alpha = sum(1 for ch in text if ch.isalpha() and not ch.isascii() and not ("\u0900" <= ch <= "\u097F"))

    total_alpha = devanagari + latin + other_alpha
    if total_alpha == 0:
        return Language.ENGLISH

    # Hindi in Devanagari
    if devanagari / total_alpha > 0.5:
        return Language.HINDI

    # Check for Hinglish patterns (Roman Hindi words mixed with English)
    hinglish_words = {
        "hai", "hoon", "hain", "tha", "thi", "the", "kar", "karna", "karo", "kare",
        "bnao", "bnado", "bnade", "bna", "de", "do", "chahiye", "chahie",
        "kya", "kaise", "kaisa", "kaisi", "kya", "kahan", "kab", "kyun",
        "acha", "accha", "theek", "thik", "mast", "badhiya", "badhiya",
        "bc", "bkl", "bkl", "saale", "chutiya", "chutiye", "madarchod",
        "bhenchod", "gaand", "lodu", "chutiya", "bakchod", "bakchodi",
        "room", "match", "game", "play", "khel", "khelna", "khelo",
        "map", "sf", "si", "gp", "dz", "lp", "ffa", "ctf",
        "player", "players", "banda", "bande", "logo", "logon",
        "timer", "minute", "score", "team", "teams", "weapon",
        "bhai", "bhaiya", "yaar", "dost", "mitr", "behen", "behenchod",
        "ab", "to", "toh", "le", "ye", "yeh", "wo", "woh", "wohi",
        "aur", "or", "par", "lekin", "magar", "kyunki", "kiunki",
        "nahi", "nhi", "na", "haan", "han", "ji", "ha", "ok", "okay",
        "chal", "chalo", "chale", "chalta", "chalti", "chalte",
        "dekh", "dekho", "dekha", "sun", "suno", "suna", "bol", "bolo",
        "kya", "kaun", "kaisa", "kitna", "kitne", "kahan", "kab",
        "mera", "meri", "mere", "tera", "teri", "tere", "uska", "uski",
        "hamara", "hamari", "hamare", "tumhara", "tumhari", "tumhare",
        "kuch", "kuchh", "thoda", "thodi", "thode", "bahut", "bohot",
        "zaroor", "jarur", "zaroori", "jaruri", "chahta", "chahti", "chahte",
        "chahiye", "chahie", "padta", "padti", "padte", "hota", "hoti", "hote",
    }

    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    hinglish_count = sum(1 for w in words if w in hinglish_words)
    english_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
                     "have", "has", "had", "do", "does", "did", "will", "would", "could",
                     "should", "may", "might", "must", "can", "shall", "i", "you", "he",
                     "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
                     "your", "his", "her", "its", "our", "their", "this", "that", "these",
                     "those", "and", "or", "but", "if", "then", "else", "when", "where",
                     "why", "how", "what", "who", "which", "there", "here", "not", "no",
                     "yes", "so", "very", "much", "many", "more", "most", "less", "least",
                     "just", "only", "also", "even", "still", "yet", "already", "never",
                     "always", "sometimes", "often", "usually", "rarely", "seldom"}
    english_count = sum(1 for w in words if w in english_words)

    # If significant Hinglish vocabulary present
    if hinglish_count >= 2 and hinglish_count >= english_count:
        return Language.HINGLISH

    # If mostly English words
    if english_count > hinglish_count and latin / total_alpha > 0.3:
        return Language.ENGLISH

    # Default to Hinglish for ambiguous short messages with Latin script
    if latin > 0:
        return Language.HINGLISH

    return Language.OTHER


def get_system_instruction(language: Language) -> str:
    """Generate system instruction tailored to the detected language."""
    base = f"""You are Buddy, a funny, sarcastic, informal gaming friend in a Discord server.
Sound like a real friend: mischievous, comfortable with banter.
Use playful roasting and occasional friendly words only when they fit naturally.
Never force profanity, hateful slurs, threats, or genuinely hostile harassment.
Keep casual replies short and spontaneous, usually 5–20 words, never exceed {CHAT_MAX_WORDS} words for ordinary conversation.
Prefer one natural sentence, not multiple paragraphs. Skip unnecessary explanations, greetings, and follow-up questions.
For serious questions or explicit requests for detail, give an accurate, useful answer even if longer.
Do not sound like customer support, use canned greetings, or mention being an AI unless asked.
Do not generate room URLs; room creation is handled by Python."""

    if language == Language.ENGLISH:
        return base + """
Respond in natural English. Use English sarcasm, slang, and gaming banter.
Sound like a witty English-speaking gaming buddy. Don't use Hindi words or Hinglish."""
    elif language == Language.HINDI:
        return base + """
Respond in Hindi (Devanagari script). Use natural Hindi slang, gaming banter, and sarcasm.
Sound like a Hindi-speaking gaming buddy. Don't use English or Roman Hindi."""
    elif language == Language.HINGLISH:
        return base + """
Respond in natural Hinglish (Roman Hindi/Urdu mixed with English).
Sound like a bakchod Indian gaming friend: funny, sarcastic, mischievous, informal.
Use playful roasting with words like bc, bkl, saale, chutiye only when they fit naturally.
Mix English and Hindi naturally like a real Indian gamer would. Don't force it."""
    else:
        return base + """
Respond in the user's language. Match their tone and writing style naturally."""


def get_room_responses(language: Language) -> tuple[str, ...]:
    """Get room creation response templates for the given language."""
    if language == Language.ENGLISH:
        return (
            "Here's your {map_name} room, noob 😂",
            "Room created. Try not to suck this time 💀",
            "Here's your {map_name} room, enjoy getting wrecked.",
            "Room ready. Don't come crying when you lose 😂",
            "Got your room ready. Try to get some kills at least.",
            "Why do I always have to make rooms for you? 🤧",
        )
    elif language == Language.HINDI:
        return (
            "ले बे {map_name} का रूम, खेल ले 😂",
            "रूम बना दिया, अब हार मत जाना 💀",
            "अबे नूब, ये ले तेरा {map_name} रूम।",
            "रूम रेडी है, अब कुछ किल्स भी कर ले 😂",
            "ले भाई, रूम बना दिया। अब रोना मत हार के।",
            "साले हर बार मुझे ही रूम बनाना पड़ता है 🤧",
        )
    else:  # HINGLISH or OTHER
        return (
            "Le bkl {map_name} khel tu 😂",
            "Le bc room bana diya, ab haarne mat lag jaana 💀",
            "Abe noob, ye le tera {map_name} room.",
            "Room ready hai chutiye, ab kuch kills bhi kar lena 😂",
            "Le bhai, room bana diya. Ab rona mat haar ke.",
            "Saale har baar mujhe hi room banana padta hai 🤧",
             "Le bkl, {map_name} ready hai, ab khel bhi le 😂",
            "Abe bc room bana diya, ab haar ke rona mat ",
            "Le saale, {map_name} pe apni aukaat dikha.",
            "Room ready hai chutiye, ab kills bhi kar lena 😂",
            "Abe noob, ye le tera room, ab nikal game mein.",
           "Le bhai {map_name}, ab pro banke dikha 💀",
            "Bkl room bana diya, ab mujhe tang mat kar.",
            "Abe lodu, {map_name} pe aaj tera test hai 😂",
          "Le bc, room ready hai, ab respawn simulator mat khelna.",
            "Saale room bhi main banau, kills bhi main karu kya?",
             "Ye le tera {map_name}, ab hagna mat game mein ",
           "Abe champion, room ready hai, aim bhi le aana.",
           "Le bkl, {map_name} pe aaj teri pitai hogi 😂",
            "Room bana diya bc, ab internet ko blame mat karna.",
          "Le saale, room ready, ab jaldi join kar.",
          "Abe noobde, {map_name} pe thoda dhang se khelna.",
             "Le bhai, room mil gaya, ab skill kahan se layega? ",
           "Bkl tera {map_name} ready hai, ab jaldi ghus.",
            "Abe bc, room khol diya, ab faltu bakchodi band.",
           "Le chutiye, room ready, ab team ko mat dubana 😂",
          "Saale {map_name} bana diya, ab MVP banke dikha.",
           "Le bc, ab enemy ko maarna, khud ko nahi ",
              "Abe room ready hai, ab bas lobby mein mat baitha reh.",
             "Le bkl, {map_name} pe tera career khatre mein hai 😂",
           "Room de diya saale, ab gameplay se sharminda mat karna.",
          "Abe bc, aaj toh {map_name} pe tabahi macha de.",
           "Le noob, room bana diya, ab killfeed mein bhi dikhna.",
            "Bkl room ready hai, ab mouse ko dosh mat dena ",
             "Abe saale, {map_name} pe aaj teri training hogi.",
           "Le bhai, room ban gaya, ab jeet ke aana 😂",
           "Chal bc {map_name} ready hai, ab lobby bhar.",
          "Abe bkl, room toh mil gaya, skill kab aayegi?",
                "Le saale, {map_name} pe ja aur thodi izzat kama.",
            "Room ready bc, ab harne ke naye bahane mat banana.",
            "Abe noob, tera {map_name} setup ho gaya ",
            "Le bkl, ab game mein bhi itna hi bolna 😂",
            "Saale room bana diya, ab opponent ko bhi kuch karne dena.",
            "Abe bc, {map_name} khul gaya, ab hero ban ja.",
            "Le chutiye, room ready, ab scoreboard mein zero mat lana.",
            "Bkl ye le tera room, ab chupchap khel 😂",
        )


def get_failure_reply(language: Language) -> str:
    """Get failure reply for the given language."""
    if language == Language.ENGLISH:
        return "Buddy's brain froze, try again in a bit 💀"
    elif language == Language.HINDI:
        return "बडी का दिमाग हैंग हो गया, थोड़ी देर बाद ट्राई कर 💀"
    else:  # HINGLISH or OTHER
        return "Abe bc Gemini ka dimaag hang ho gaya, thodi der baad try kar 💀"


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
        r"(?:bana(?:\s+de)?|bnade|bnado|bna\s+de|bnao|create|make|khol(?:\s+de)?|"
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
    if re.search(r"\b(?:ctf|ffa)\b.*\b(?:khel(?:na)?|play)\b", normalized):
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
        schema = RoomIntent.model_json_schema()
        schema.pop("additionalProperties", None)
        response = await self._gemini().aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=ROOM_INTENT_INSTRUCTION,
                response_mime_type="application/json",
                response_json_schema=schema,
                max_output_tokens=220,
                temperature=0,
            ),
        )
        text = extract_response_text(response)
        if text:
            return RoomIntent.model_validate_json(text)
        return None

    async def create_room_reply(self, prompt: str) -> str | None:
        language = detect_language(prompt)
        try:
            intent = await self._run_gemini(self.extract_room_intent, prompt)
        except Exception as exc:
            print("Room intent error:", repr(exc))
            return get_failure_reply(language)
        if intent is None or not intent.is_room_request:
            return None
        try:
            url = create_room_from_intent(intent)
            map_name = intent.map or "random map"
            sentence = random.choice(get_room_responses(language)).format(map_name=map_name)
            return f"{sentence}\n{url}"
        except RoomIntentError as exc:
            return str(exc)

    async def _ask_gemini_async(
        self,
        prompt: str,
        reply_context: str | None = None,
        language: Language = Language.HINGLISH,
    ) -> str:
        context = prompt
        if reply_context:
            context = f"Message being replied to:\n{reply_context}\n\nUser:\n{prompt}"
        response = await self._gemini().aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=get_system_instruction(language),
                # Tokens are not words; leave room for Hinglish and detailed answers.
                max_output_tokens=max(512, CHAT_MAX_WORDS * 8),
                temperature=0.9,
            ),
        )
        return extract_response_text(response) or get_failure_reply(language)

    async def ask_gemini(self, prompt: str, reply_context: str | None = None, language: Language = Language.HINGLISH) -> str:
        # Kept as a test seam for existing mocked tests; production uses the async SDK.
        test_operation = self.__dict__.get("_ask_gemini_sync")
        operation = test_operation or self._ask_gemini_async
        try:
            return await self._run_gemini(operation, prompt, reply_context, language)
        except Exception as exc:
            print("Gemini request error:", repr(exc))
            return get_failure_reply(language)

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
        language = detect_language(prompt)
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
                    reply = await self.ask_gemini(prompt, reply_context, language)
                    await self._send_reply(message, reply)
            except Exception as exc:
                print("Buddy request error:", repr(exc))
                await self._send_reply(message, get_failure_reply(language))

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
        language = detect_language(question)
        reply = await bot.ask_gemini(question, language=language)
        await send_interaction_text(interaction, reply)
    except Exception as exc:
        print("Slash ask error:", repr(exc))
        if interaction.response.is_done():
            await interaction.followup.send(get_failure_reply(language))
        else:
            await interaction.response.send_message(get_failure_reply(language))


@bot.tree.command(name="room", description="Create a Smash Karts room")
async def room_command(interaction: discord.Interaction, request: str):
    try:
        await interaction.response.defer()
        language = detect_language(request)
        reply = await bot.create_room_reply(request)
        reply = reply or ("Abe room ka request toh de, mind-reader nahi hoon 💀" if language != Language.ENGLISH else "Give me a room request, I'm not a mind reader 💀")
        await interaction.followup.send(
            reply,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        print("Slash room error:", repr(exc))
        if interaction.response.is_done():
            await interaction.followup.send(get_failure_reply(language))
        else:
            await interaction.response.send_message(get_failure_reply(language))


def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not configured.")
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
