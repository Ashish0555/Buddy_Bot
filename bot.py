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
You are Buddy, a fun, sarcastic gaming friend in a Discord server.
You speak English, Hinglish, Hindi, Tamil, Telugu - always match the user's language.
Sound like a real friend: funny, witty, casual, comfortable with banter.
Use light teasing and playful roasting ONLY when the user initiates that vibe
or explicitly asks for a roast. Never force profanity, hateful slurs, threats,
or genuinely hostile harassment. Don't start every response with "abe", "bc",
"bkl", "saale", or "chutiye" - save those for actual banter moments.
Keep casual replies short and spontaneous, usually 5–20 words, never exceed {CHAT_MAX_WORDS} words for ordinary conversation. Prefer one natural sentence,
not multiple paragraphs. Skip unnecessary explanations, greetings, and
follow-up questions. For serious questions or explicit requests for detail,
give an accurate, useful answer even if it needs to be longer.
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

# Friendly, varied room response templates - mostly helpful, some playful for banter
ROOM_RESPONSES_ENGLISH = (
    "Here's your {map_name} room!",
    "Room ready — have fun!",
    "Your {map_name} room is set up.",
    "All set! {map_name} room link below.",
    "Room created. Enjoy the game!",
    "Done! Here's your {map_name} room.",
)

ROOM_RESPONSES_HINDI = (
    "ये रहा तुम्हारा {map_name} रूम!",
    "रूम तैयार है, मजे करो!",
    "तुम्हारा {map_name} रूम सेट हो गया।",
    "लो, {map_name} रूम का लिंक।",
    "रूम बन गया। गेम एन्जॉय करो!",
    "हो गया! ये रहा {map_name} रूम।",
)

ROOM_RESPONSES_HINGLISH = (
    "Le bhai, {map_name} room ready hai!",
    "Room bana diya, enjoy karo!",
    "Tera {map_name} room set ho gaya.",
    "Ye raha {map_name} room ka link.",
    "Room ready hai. Game enjoy kar!",
    "Ho gaya! Ye raha tera {map_name} room.",
)

# Playful/banter responses - only used when user engages in banter first
ROOM_RESPONSES_PLAYFUL_ENGLISH = (
    "Here's your {map_name} room, try not to suck 😂",
    "Room ready. Don't come crying when you lose!",
    "Got your room. Try to get at least one kill 💀",
)

ROOM_RESPONSES_PLAYFUL_HINDI = (
    "Le {map_name} ka room, ab haar mat jaana 😂",
    "Room ready hai, ab rona mat haar ke!",
    "Room bana diya, kuch kills toh kar lena 💀",
)

ROOM_RESPONSES_PLAYFUL_HINGLISH = (
    "Le bkl {map_name} khel tu 😂",
    "Room ready hai chutiye, ab kuch kills bhi kar lena 😂",
    "Le bc room bana diya, ab haar ke rona mat 💀",
)

GEMINI_FAILURE_REPLY_ENGLISH = "Buddy's brain froze, try again in a bit 💀"
GEMINI_FAILURE_REPLY_HINDI = "बडी का दिमाग हैंग हो गया, थोड़ी देर बाद ट्राई कर 💀"
GEMINI_FAILURE_REPLY_HINGLISH = "Abe bc Gemini ka dimaag hang ho gaya, thodi der baad try kar 💀"

# Backwards compatibility
GEMINI_FAILURE_REPLY = GEMINI_FAILURE_REPLY_ENGLISH

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
        "kya", "kaise", "kaisa", "kaisi", "kahan", "kab", "kyun",
        "acha", "accha", "theek", "thik", "mast", "badhiya",
        "bhai", "bhaiya", "yaar", "dost", "mitr", "behen",
        "ab", "to", "toh", "le", "ye", "yeh", "wo", "woh", "wohi",
        "aur", "or", "par", "lekin", "magar", "kyunki", "kiunki",
        "nahi", "nhi", "na", "haan", "han", "ji", "ha",
        "chal", "chalo", "chale", "chalta", "chalti", "chalte",
        "dekh", "dekho", "dekha", "sun", "suno", "suna", "bol", "bolo",
        "kaun", "kaisa", "kitna", "kitne",
        "mera", "meri", "mere", "tera", "teri", "tere", "uska", "uski",
        "hamara", "hamari", "hamare", "tumhara", "tumhari", "tumhare",
        "kuch", "kuchh", "thoda", "thodi", "thode", "bahut", "bohot",
        "zaroor", "jarur", "zaroori", "jaruri", "chahta", "chahti", "chahte",
        "chahiye", "chahie", "padta", "padti", "padte", "hota", "hoti", "hote",
        # Gaming/room related words commonly used in Hinglish
        "room", "match", "game", "play", "khel", "khelna", "khelo",
        "map", "sf", "si", "gp", "dz", "lp", "ffa", "ctf",
        "player", "players", "banda", "bande", "logo", "logon",
        "timer", "minute", "score", "team", "teams", "weapon",
        "please", "pls", "plz",
        # Common Hinglish grammar words
        "ka", "ke", "ki", "ko", "me", "se", "pe", "par", "liye",
        # Slang/bakchod words for Hinglish detection
        "bakchod", "bakchodi",
        # Common shortforms/slang
        "bkl", "bk", "bna", "bn", "bnao", "bnae", "bnaega", "bnaegi",
    }

    # English words that should NOT count as Hinglish even if in hinglish_words
    # (e.g., "ok", "okay" are common in English too)
    hinglish_exclude = {"ok", "okay"}

    # Gaming terms that are common in both English and Hinglish
    # Should not count as Hinglish indicators on their own
    gaming_terms = {"room", "match", "game", "play", "map", "sf", "si", "gp", "dz", "lp",
                    "ffa", "ctf", "player", "players", "timer", "minute", "score",
                    "team", "teams", "weapon", "random"}

    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    hinglish_count = sum(1 for w in words if w in hinglish_words and w not in hinglish_exclude and w not in gaming_terms)

    # Common English stop words that shouldn't count as "English content"
    english_stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
                         "have", "has", "had", "do", "does", "did", "will", "would", "could",
                         "should", "may", "might", "must", "can", "shall", "i", "you", "he",
                         "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
                         "your", "his", "her", "its", "our", "their", "this", "that", "these",
                         "those", "and", "or", "but", "if", "then", "else", "when", "where",
                         "why", "how", "what", "who", "which", "there", "here", "not", "no",
                         "yes", "so", "very", "much", "many", "more", "most", "less", "least",
                         "just", "only", "also", "even", "still", "yet", "already", "never",
                         "always", "sometimes", "often", "usually", "rarely", "seldom"}

    # Count meaningful English words (non-stopwords, not in hinglish_words)
    english_content_words = [w for w in words if w not in english_stopwords and w not in hinglish_words and len(w) > 2]
    english_count = len(english_content_words)

    # If significant Hinglish vocabulary present (at least 2 words, more than English content)
    if hinglish_count >= 2 and hinglish_count >= english_count:
        return Language.HINGLISH

    # If mostly English content words
    if english_count > hinglish_count and latin / total_alpha > 0.3:
        return Language.ENGLISH

    # For short messages with only common English words (hi, hey, hello, thanks, ok, etc.)
    # and no Hinglish markers, default to English
    if hinglish_count == 0 and english_count > 0:
        return Language.ENGLISH

    # Check if it's a simple greeting or common English phrase
    simple_english = {"hi", "hey", "hello", "thanks", "thank", "ok", "okay", "yes", "no", "yo", "sup", "hru", "gm", "gn", "bye", "good", "morning", "night", "how", "are", "you", "doing", "what", "up"}
    if words.issubset(simple_english | english_stopwords) and hinglish_count == 0:
        return Language.ENGLISH

    # Default to English for ambiguous short messages with Latin script
    # (only if no Hinglish markers found)
    if latin > 0 and hinglish_count == 0:
        return Language.ENGLISH

    return Language.OTHER


def get_system_instruction(language: Language) -> str:
    """Generate system instruction tailored to the detected language."""
    base = f"""You are Buddy, a fun, sarcastic, witty gaming friend in a Discord server.
Sound like a real friend: casual, comfortable with banter.
Use light teasing and playful roasting ONLY when the user initiates that vibe
or explicitly asks for a roast. Never force profanity, hateful slurs, threats,
or genuinely hostile harassment. Don't automatically start with "abe", "bc",
"bkl", "saale", or "chutiye" - save those for actual banter moments.
Keep casual replies short and spontaneous, usually 5–20 words, never exceed {CHAT_MAX_WORDS} words for ordinary conversation. Prefer one natural sentence,
not multiple paragraphs. Skip unnecessary explanations, greetings, and
follow-up questions. For serious questions or explicit requests for detail,
give an accurate, useful answer even if longer.
Do not sound like customer support, use canned greetings, or mention being an
AI unless asked. Do not generate room URLs; room creation is handled by Python."""

    if language == Language.ENGLISH:
        return base + """
Respond in natural English. Use English sarcasm, slang, and gaming banter.
Sound like a witty English-speaking gaming buddy. Don't use Hindi words or Hinglish.
Only roast if the user starts it or asks for a roast."""
    elif language == Language.HINDI:
        return base + """
Respond in Hindi (Devanagari script). Use natural Hindi slang, gaming banter, and sarcasm.
Sound like a Hindi-speaking gaming buddy. Don't use English or Roman Hindi.
Only roast if the user starts it or asks for a roast."""
    elif language == Language.HINGLISH:
        return base + """
Respond in natural Hinglish (Roman Hindi/Urdu mixed with English).
Sound like a fun Indian gaming friend: witty, casual, mischievous, bakchod.
Use playful roasting with words like bc, bkl, saale, chutiye ONLY when the user
starts that banter or explicitly asks for a roast. Mix English and Hindi naturally.
Don't force it or overdo the roasting."""
    else:
        return base + """
Respond in the user's language. Match their tone and writing style naturally."""


def get_room_responses(language: Language, playful: bool = False) -> tuple[str, ...]:
    """Get room creation response templates for the given language."""
    if language == Language.ENGLISH:
        if playful:
            return ROOM_RESPONSES_PLAYFUL_ENGLISH
        return ROOM_RESPONSES_ENGLISH
    elif language == Language.HINDI:
        if playful:
            return ROOM_RESPONSES_PLAYFUL_HINDI
        return ROOM_RESPONSES_HINDI
    else:  # HINGLISH or OTHER
        if playful:
            return ROOM_RESPONSES_PLAYFUL_HINGLISH
        return ROOM_RESPONSES_HINGLISH


def get_failure_reply(language: Language) -> str:
    """Get failure reply for the given language."""
    if language == Language.ENGLISH:
        return GEMINI_FAILURE_REPLY_ENGLISH
    elif language == Language.HINDI:
        return GEMINI_FAILURE_REPLY_HINDI
    else:  # HINGLISH or OTHER
        return GEMINI_FAILURE_REPLY_HINGLISH


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
            # Check if user is engaging in playful banter (contains roast triggers)
            is_playful = self._is_playful_context(prompt, language)
            sentence = random.choice(get_room_responses(language, playful=is_playful)).format(map_name=map_name)
            return f"{sentence}\n{url}"
        except RoomIntentError as exc:
            return str(exc)

    def _is_playful_context(self, prompt: str, language: Language) -> bool:
        """Detect if user is engaging in playful banter/roasting."""
        lower = prompt.lower()
        # Explicit roast requests
        if re.search(r"\b(?:roast|insult|abuse)\s+me\b", lower):
            return True
        # User using heavy slang/roasting first
        if language == Language.HINGLISH:
            roast_words = {"bc", "bkl", "saale", "chutiye", "madarchod", "bhenchod", "lodu", "bakchod"}
            words = set(re.findall(r"[a-zA-Z]+", lower))
            if words & roast_words:
                return True
        return False

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

    async def ask_gemini(self, prompt: str, reply_context: str | None = None, language: Language | None = None) -> str:
        # Kept as a test seam for existing mocked tests; production uses the async SDK.
        # Auto-detect language if not provided
        if language is None:
            language = detect_language(prompt)
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
        if reply is None:
            if language == Language.ENGLISH:
                reply = "Give me a room request, I'm not a mind reader 💀"
            elif language == Language.HINDI:
                reply = "रूम का रिक्वेस्ट तो दे, माइंड रीडर नहीं हूँ 💀"
            else:
                reply = "Abe room ka request toh de, mind-reader nahi hoon 💀"
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