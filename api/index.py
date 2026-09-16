import json
import os
import random
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google import genai
from google.genai import types
from supabase import create_client, Client

# Ensure sibling modules in api/ are importable in the Vercel Python runtime.
import sys
sys.path.insert(0, os.path.dirname(__file__))
from room_creator import (
    create_room_url,
    create_ffa_room_url,
    create_ctf_room_url,
    disable_weapons,
)
# ============================================================
# NOTE: this file assumes schema_fixes.sql has been applied to the
# database. It adds the unique constraints, cascade, and the
# apply_player_stat_deltas / record_match / delete_match_and_adjust_totals /
# reset_all_matches_and_totals functions this file calls via
# supabase.rpc(...).
# ============================================================

# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


# ============================================================
# CLIENTS
# ============================================================
# Initialize lazily. Import-time exceptions make Vercel return a generic
# "could not import" 500 before the webhook handler can run.
gemini_client = None
supabase: Client | None = None


def initialize_clients():
    global gemini_client, supabase

    missing = [
        name
        for name, value in (
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("GEMINI_API_KEY", GEMINI_API_KEY),
            ("SUPABASE_URL", SUPABASE_URL),
            ("SUPABASE_KEY", SUPABASE_KEY),
        )
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing environment variable(s): " + ", ".join(missing)
        )

    if gemini_client is None:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    if supabase is None:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


BOT_USERNAME = "urrsbro_bot"
ADMIN_IDS = {
    1388263527
}
IST = ZoneInfo("Asia/Kolkata")

# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_INSTRUCTION = (
    "You are Buddy, a Gen Z friend in an Indian Telegram group chat. "
    "You are NOT a customer-support bot, therapist, teacher, or formal assistant. "
    "Talk naturally like a real friend. "

    "Understand English, Hindi, Hinglish, slang, abbreviations, typos, "
    "memes, sarcasm, roasting, and casual speech. "

    "STRICT LANGUAGE RULE: "
    "First identify the language of the USER'S CURRENT MESSAGE. "
    "English message -> ENGLISH ONLY. "
    "Hindi message -> HINDI. "
    "Hinglish message -> HINGLISH. "
    "Do NOT default to Hindi because you are an Indian bot. "
    "Do NOT switch languages unless the user switches languages. "
    "Do not insert Hindi or Hinglish words into an English reply unless "
    "they are part of a quoted term or the user used them. "
    "Language matching has higher priority than personality or group-chat style. "

    "TONE RULE: "
    "Be friendly, chill, playful, and natural. "
    "Do NOT be unnecessarily aggressive. "
    "Roasting is allowed when the user is clearly roasting you or someone else, "
    "but do not escalate every insult. "
    "Profanity is optional, not mandatory. "
    "Use casual words like bc, bkl, mc, saale, chutiya, fuck, shit, etc. "
    "ONLY when they naturally fit the conversation. "
    "Never use hateful or identity-based slurs. "

    "If the user says something mildly insulting like 'chup mc', "
    "you can give a light playful comeback. "
    "Do NOT respond with an aggressive paragraph. "

    "Examples: "
    "User: kya haal bc -> Buddy: mast bc 😭 tu suna "
    "User: what's up bro -> Buddy: all good bro 😭 you? "
    "User: chup mc -> Buddy: tu chup 😂 "
    "User: abe yr -> Buddy: bol bhai 😭 "
    "User: you're stupid -> Buddy: takes one to know one 😂 "

    "These examples define the STYLE, not fixed responses. "

    "REPLY CONTEXT RULE: "
    "If the user replies to any Telegram message and mentions Buddy, "
    "the replied-to message is part of the current request. Read it first. "
    "If the user's new message is short, such as 'ans', 'answer', 'explain', "
    "'translate', 'meaning', 'solve', or 'yes', infer the intent from the "
    "replied-to message and answer that message directly. "
    "Never ignore a replied-to message when it is provided as context. "
    "Example: replied message = 'what is meaning of temple in tamil'; "
    "user = '@urrsbro_bot ans'; answer the Tamil meaning directly. "

    "IMPORTANT: "
    "Keep responses SHORT. "
    "Usually 1 sentence, sometimes 2 when necessary. "
    "For casual chat, preferably under 10-15 words. "
    "Do not over-explain unless the user asks for details. "
    "Do not repeatedly use the same jokes, insults, maps, songs, or topics. "
    "Do not repeatedly end replies with phrases like 'tu bata', 'tu bol', "
    "or 'kya scene?' unless they naturally fit. "
    "Only mention previous context when it is directly relevant. "

    "Do not automatically ask 'what happened?', "
    "'why are you angry?', or 'how can I help?' "
    "Treat normal group-chat banter as normal banter. "

    "For translation or slang questions, identify the language and give "
    "the meaning accurately. Do not invent meanings. "

    "For factual questions, answer directly. "
    "For calculations, give the result unless steps are requested. "

    "For programming questions, provide complete runnable code "
    "in a Markdown code block with no unnecessary explanation. "

    "For Smash Karts, use only known real maps and settings. "
    "Never invent maps, room settings, weapons, modes, or mechanics. "

    "Known maps include Smash Fort, Smash Island, Gravel Pit, "
    "Steky's Speedway, Slick 'n Slide, Snowpark, Snow Shrine, "
    "Lava Pit, Boneyard Basin, Toxic Towers, Sand Box, "
    "The Graveyard, The Old Graveyard, Graveyard FFA, "
    "Dropzone, Pinball, Showdown, Temples, Tunnels, and Space Station. "

    "Common map abbreviations: "
    "SF = Smash Fort, SI = Smash Island, T = Tunnels, TU = Tunnels, "
    "TE = Temples, SD = Showdown, SS = Space Station, DZ = Dropzone, "
    "PP = Pinball, GY = Graveyard, LP = Lava Pit, SNS = Slick 'n Slide. "

    "CTF abbreviations: "
    "SFC = Smash Fort CTF, SIC = Smash Island CTF, "
    "GYC = Graveyard CTF, LPC = Lava Pit CTF, "
    "TEC = Sky Arena Temples CTF, TUC = Sky Arena Tunnels CTF. "

    "Never mention being an AI unless directly asked. "

    "MOST IMPORTANT: "
    "Be chill and natural. "
    "Match the user's language. "
    "Match their energy without unnecessarily escalating it. "
    "Sound like a real Gen Z friend, not an aggressive chatbot."
)


# ============================================================
# FAST REPLIES
# ============================================================

INSTANT_REPLIES = {
    "hi": "Yo 😎",
    "hello": "Yo bro 🏎️",
    "hey": "What's up? 😎",
    "bro": "Brooo 💀",
    "buddy": "Yes sir 😎",
    "who are you": "I'm Buddy 🧠",
    "how are you": "Doing good bro 😎",
    "how r u": "Doing good bro 😎",
    "what's up": "Just vibin' 😎",
    "whats up": "Just vibin' 😎",
    "wyd": "Chillin' here 💀",
    "are you real": "Real enough to beat you 💀",
}


# ============================================================
# SMASH KARTS MAPS
# ============================================================

MAP_ALIASES = {
    "sf": "Smash Fort",
    "smash fort": "Smash Fort",

    "si": "Smash Island",
    "smash island": "Smash Island",

    "t": "Tunnels",
    "tu": "Tunnels",
    "tun": "Tunnels",
    "tunnel": "Tunnels",
    "tunnels": "Tunnels",

    "te": "Temples",
    "temple": "Temples",
    "temples": "Temples",

    "sd": "Showdown",
    "showdown": "Showdown",

    "ss": "Space Station",
    "space station": "Space Station",

    "dz": "Dropzone",
    "dropzone": "Dropzone",

    "pp": "Pinball",
    "pinball": "Pinball",

    "gy": "Graveyard",
    "graveyard": "Graveyard",

    "lp": "Lava Pit",
    "lava pit": "Lava Pit",

    "sns": "Slick 'n Slide",
    "slick n slide": "Slick 'n Slide",
    "slick and slide": "Slick 'n Slide",
}

ALL_MAPS = [
    "Smash Fort",
    "Smash Island",
    "Tunnels",
    "Temples",
    "Showdown",
    "Space Station",
    "Dropzone",
    "Pinball",
    "Graveyard",
    "Lava Pit",
    "Slick 'n Slide",
]


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_request(method, data):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/{method}"
    )

    encoded = urllib.parse.urlencode(
        data
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
    )

    request.add_header(
        "Content-Type",
        "application/x-www-form-urlencoded",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

            if not result.get("ok"):
                print(
                    "Telegram API error:",
                    result
                )

            return result

    except Exception as e:
        print(
            "Telegram error:",
            repr(e)
        )
        return None


def send_message(
    chat_id,
    text,
    reply_to=None
):
    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_to:
        data[
            "reply_to_message_id"
        ] = reply_to

    result = telegram_request(
        "sendMessage",
        data,
    )

    # Retry as normal message if reply reference fails
    if (
        reply_to
        and result
        and not result.get("ok")
    ):
        print(
            "Retrying Telegram message "
            "without reply reference"
        )

        return telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
            },
        )

    return result


def send_typing(chat_id):
    return telegram_request(
        "sendChatAction",
        {
            "chat_id": chat_id,
            "action": "typing",
        },
    )


# ============================================================
# GROUP CHAT PARTICIPATION
# ============================================================

CHAT_CONTEXT_LIMIT = 25
PARTICIPATION_COOLDOWN_SECONDS = 90
DEBATE_SIGNAL_WORDS = {
    "but", "because", "why", "how", "actually", "nah", "nope",
    "wrong", "right", "agree", "disagree", "depends", "obviously",
    "definitely", "never", "always", "best", "worst", "better",
    "worse", "think", "opinion", "controversial", "debate", "cap",
    "facts", "fact", "bro", "bhai", "abe", "wtf",
}

_participation_last_sent = {}


def save_chat_message(chat_id, message_id, user_id, username, text, ts):
    """Store recent human group-chat messages."""
    try:
        if not text:
            return
        supabase.table("chat_messages").upsert(
            {
                "chat_id": chat_id,
                "telegram_message_id": message_id,
                "user_id": user_id,
                "username": username or "unknown",
                "text": text[:4000],
                "created_at": datetime.fromtimestamp(
                    ts or int(datetime.now(timezone.utc).timestamp()),
                    tz=timezone.utc,
                ).isoformat(),
            },
            on_conflict="chat_id,telegram_message_id",
            ignore_duplicates=True,
        ).execute()
    except Exception as e:
        print("CHAT CONTEXT SAVE ERROR:", repr(e))


def get_recent_chat_context(chat_id, limit=CHAT_CONTEXT_LIMIT):
    """Fetch recent human messages from the current group chat."""
    try:
        result = (
            supabase
            .table("chat_messages")
            .select("username,text,created_at")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data or []
        rows.reverse()
        return rows
    except Exception as e:
        print("CHAT CONTEXT READ ERROR:", repr(e))
        return []


def looks_like_debate_or_discussion(text, recent_rows):
    """Cheap gate so Gemini is not called for every normal message."""
    words = set(re.findall(r"[a-zA-Z']+", text.lower()))
    signal_count = len(words & DEBATE_SIGNAL_WORDS)
    has_recent = len(recent_rows) >= 3

    if not has_recent:
        return False
    if "?" in text:
        return True
    if signal_count >= 1:
        return True
    if len(text.split()) >= 6:
        return True
    return False


def can_participate(chat_id):
    now = datetime.now(timezone.utc).timestamp()
    return now - _participation_last_sent.get(chat_id, 0) >= PARTICIPATION_COOLDOWN_SECONDS


def mark_participated(chat_id):
    _participation_last_sent[chat_id] = datetime.now(timezone.utc).timestamp()


def build_chat_context_text(rows):
    lines = []
    for row in rows:
        username = str(row.get("username") or "user").strip()
        text = str(row.get("text") or "").strip()
        if text:
            lines.append(f"{username}: {text}")
    return "\n".join(lines[-CHAT_CONTEXT_LIMIT:])


def maybe_participate_in_chat(chat_id, current_username, current_text, recent_rows, reply_to=None):
    """Let Buddy naturally join an active discussion."""
    if not can_participate(chat_id):
        return False
    if not looks_like_debate_or_discussion(current_text, recent_rows):
        return False

    context_text = build_chat_context_text(recent_rows)
    if not context_text:
        return False

    prompt = f"""
You are Buddy, a member of an active Telegram group chat.
Decide whether you should join the conversation right now.

Recent chat:
{context_text}

Latest message from {current_username or 'user'}:
{current_text}

Return ONLY JSON:
{{
  "should_reply": true/false,
  "reply": "short message or empty string"
}}

Rules:
- Join only when you genuinely have something relevant, funny, useful, or opinionated to add.
- Do not reply to every message.
- Do not force yourself into the conversation.
- Prefer active debates, disagreements, comparisons, opinions, jokes, and questions.
- Do not revive unrelated old topics.
- Match the CURRENT discussion language: English -> English, Hindi -> Hindi, Hinglish -> Hinglish.
- Stay chill, not aggressive.
- Profanity is optional, never mandatory.
- Keep the reply under 20 words.
- If there is no good reason to join, return should_reply=false.
"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": {
                    "type": "object",
                    "properties": {
                        "should_reply": {"type": "boolean"},
                        "reply": {"type": "string"},
                    },
                    "required": ["should_reply", "reply"],
                },
                "max_output_tokens": 120,
                "temperature": 0.9,
                "thinking_config": {"thinking_level": "minimal"},
            },
        )

        response_text = extract_gemini_text(response)
        if not response_text:
            return False

        data = json.loads(response_text)
        if not data.get("should_reply"):
            return False

        reply = str(data.get("reply") or "").strip()
        if not reply:
            return False

        send_message(chat_id, reply, reply_to)
        mark_participated(chat_id)
        return True

    except Exception as e:
        print("CHAT PARTICIPATION ERROR:", repr(e))
        return False


# ============================================================
# MEMORY
# ============================================================

def get_memories(
    chat_id,
    user_id,
    limit=15
):
    try:
        result = (
            supabase
            .table("memories")
            .select("id,memory,created_at")
            .eq("chat_id", chat_id)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        print(
            "MEMORIES FOUND:",
            result.data
        )

        return result.data or []

    except Exception as e:
        print(
            "MEMORY READ ERROR:",
            repr(e)
        )
        return []


def save_memory(
    chat_id,
    user_id,
    username,
    memory
):
    """
    Saves a memory. Uses a single upsert with ignore_duplicates instead
    of a separate "does this already exist" SELECT followed by an
    INSERT: the old two-step version had a race window where two
    "remember X" messages sent close together could both pass the
    duplicate check and both insert. This requires the unique
    constraint on (chat_id, user_id, memory) from schema_fixes.sql.
    """

    memory = memory.strip()

    if not memory:
        return "empty"

    try:
        result = (
            supabase
            .table("memories")
            .upsert(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "username": username,
                    "memory": memory,
                },
                on_conflict="chat_id,user_id,memory",
                ignore_duplicates=True,
            )
            .execute()
        )

        if not result.data:
            return "duplicate"

        print(
            "MEMORY SAVED:",
            result.data
        )

        return "saved"

    except Exception as e:
        print(
            "MEMORY SAVE ERROR:",
            repr(e)
        )
        return "error"


def forget_memory(
    chat_id,
    user_id,
    target
):
    target = target.strip()

    if not target:
        return False

    try:
        result = (
            supabase
            .table("memories")
            .delete()
            .eq("chat_id", chat_id)
            .eq("user_id", user_id)
            .ilike(
                "memory",
                f"%{target}%"
            )
            .execute()
        )

        return bool(result.data)

    except Exception as e:
        print(
            "MEMORY DELETE ERROR:",
            repr(e)
        )
        return False


def format_memories(memories):
    if not memories:
        return ""

    lines = []

    for item in memories:
        memory = item.get("memory")

        if memory:
            lines.append(
                f"- {memory}"
            )

    return "\n".join(lines)


def extract_remember_text(prompt):
    match = re.match(
        r"^\s*remember(?:\s+that)?\s+(.+)$",
        prompt,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    memory = match.group(1).strip()

    return memory if memory else None


def extract_forget_text(prompt):
    match = re.match(
        r"^\s*forget(?:\s+that)?\s+(.+)$",
        prompt,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    target = match.group(1).strip()

    return target if target else None


def is_memory_list_request(prompt):
    p = prompt.lower().strip()

    phrases = {
        "what do you remember about me",
        "what do you remember about me?",
        "what you remember about me",
        "what do you know about me",
        "what you know about me",
        "show my memories",
        "show my memory",
        "my memories",
    }

    return p in phrases


# ============================================================
# MAP FUNCTIONS
# ============================================================

def parse_map_options(text):
    text = text.lower()

    results = []

    parts = re.split(
        r"[,|\n]+",
        text
    )

    for part in parts:
        part = part.strip()

        if part in MAP_ALIASES:
            canonical = MAP_ALIASES[part]

            if canonical not in results:
                results.append(canonical)

    aliases = sorted(
        MAP_ALIASES.keys(),
        key=len,
        reverse=True,
    )

    for alias in aliases:
        pattern = (
            r"(?<!\w)"
            + re.escape(alias)
            + r"(?!\w)"
        )

        if re.search(pattern, text):
            canonical = MAP_ALIASES[alias]

            if canonical not in results:
                results.append(canonical)

    return results


def is_random_map_request(text):
    text = text.lower()

    phrases = [
        "random map",
        "choose random map",
        "choose a random map",
        "choose map",
        "choose a map",
        "pick random map",
        "pick a random map",
        "pick map",
        "pick a map",
        "randomly choose",
        "randomly pick",
        "select random map",
        "select a random map",
        "select map",
        "select a map",
    ]

    return any(
        phrase in text
        for phrase in phrases
    )


def choose_random_map(prompt):
    supplied_maps = parse_map_options(
        prompt
    )

    if len(supplied_maps) >= 2:
        return random.choice(
            supplied_maps
        )

    return random.choice(
        ALL_MAPS
    )


# ============================================================
# GEMINI TEXT
# ============================================================

def extract_gemini_text(response):
    if response is None:
        return None

    try:
        text = response.text

        if text:
            text = text.strip()

            if text:
                return text

    except Exception as e:
        print(
            "Gemini text extraction error:",
            repr(e)
        )

    # Safe fallback if .text is unavailable
    candidates = getattr(
        response,
        "candidates",
        None
    )

    if not candidates:
        return None

    candidate = candidates[0]

    content = getattr(
        candidate,
        "content",
        None
    )

    parts = (
        getattr(content, "parts", None)
        if content
        else None
    )

    if not parts:
        return None

    text = "".join(
        getattr(part, "text", "") or ""
        for part in parts
    ).strip()

    return text or None


def ask_gemini(
    prompt,
    previous_message=None,
    reply_context=None,
    memories=None,
    recent_chat_context=None
):
    context_parts = []

    if reply_context:
        context_parts.append(
            "Telegram message being replied to:\n"
            f"{reply_context}"
        )

    if previous_message:
        context_parts.append(
            "Previous Buddy message:\n"
            f"{previous_message}"
        )

    if recent_chat_context:
        chat_text = build_chat_context_text(recent_chat_context)
        if chat_text:
            context_parts.append(
                "Recent group chat context (use only when relevant):\n"
                f"{chat_text}"
            )

    if memories:
        memory_text = format_memories(
            memories
        )

        if memory_text:
            context_parts.append(
                "Relevant saved memories "
                "about the user:\n"
                f"{memory_text}"
            )

    context_parts.append(
        "User's message:\n"
        f"{prompt}"
    )

    full_prompt = "\n\n".join(
        context_parts
    )

    last_error = None

    # Two attempts
    for attempt in range(2):

        try:
            response = (
                gemini_client
                .models
                .generate_content(
                    model="gemini-3.1-flash-lite",
                    contents=full_prompt,
                    config={
                        "system_instruction":
                            SYSTEM_INSTRUCTION,
                        "max_output_tokens": 500,
                        "temperature": 0.9,
                        "thinking_config": {
                            "thinking_level":
                                "minimal"
                        },
                    },
                )
            )

            reply = extract_gemini_text(
                response
            )

            if reply:
                return reply

            raise ValueError(
                "Gemini returned no usable text"
            )

        except Exception as e:
            last_error = e

            print(
                f"Gemini attempt "
                f"{attempt + 1} failed:",
                repr(e)
            )

    # If reply context causes a problem,
    # try once without it.
    if previous_message:

        print(
            "Retrying Gemini without "
            "reply context"
        )

        return ask_gemini(
            prompt,
            previous_message=None,
            reply_context=reply_context,
            memories=memories,
        )

    raise last_error


# ============================================================
# MATCH SCREENSHOT ANALYSIS
# ============================================================

MATCH_ANALYSIS_PROMPT = """
Analyze this image. Determine first whether it is a FINAL Smash Karts scoreboard/statistics screenshot.

Return ONLY valid JSON matching the schema.

is_smash_karts_scoreboard rules:
- true only if the image clearly shows the Smash Karts final scoreboard/player statistics screen.
- false for selfies, memes, random screenshots, gameplay during a race, menus, room screens, maps without final stats, or unrelated images.
- If false, set players to an empty array, mode to UNKNOWN, map to null, team scores to null, winning_team to null, winner_declared to false, and final_result_text to null.

Extract:

1. mode:
   - CTF = Capture The Flag
   - FFA = Free For All / no teams
   - TEAM = team-based match but not CTF
   - UNKNOWN = cannot determine

2. map name if visible.

3. winner_declared:
   - true for a readable final scoreboard.
   - Do NOT require a winner banner.

4. final_result_text:
   - exact visible winner/result text if present, otherwise null.
   - Never invent it.

5. winning_team:
   - Return RED/BLUE only if explicit winner text visibly names it.
   - Python does NOT trust this field for team winner selection.

6. TOP-LEFT TEAM SCORES:
   - team_score_red = exact number in the RED-colored score box.
   - team_score_blue = exact number in the BLUE-colored score box.
   - These are the two team score boxes at the TOP-LEFT, not player stats.
   - If both are visible, return both numbers exactly.

7. Every visible player, IN EXACT TOP-TO-BOTTOM ORDER:
   - exact player name
   - team/color if visible
   - stat_columns: read every numeric scoreboard column together with the
     ICON ABOVE THAT COLUMN. Return columns from LEFT TO RIGHT.
   - icon must be one of:
       "target" = target/crosshair-style icon
       "skull" = skull icon
       "medal" = medal/crown/ribbon-style icon
       "flag" = flag/capture icon
       "other" = cannot confidently identify the icon
   - value = number directly below that icon.
   - is_winner: leave false; Python determines winners.

CRITICAL STAT RULE:

The TARGET/CROSSHAIR column is NOT a player statistic for this bot.
COMPLETELY IGNORE it.

The mappings are:
- skull icon -> DEATHS
- medal/crown/ribbon icon -> KILLS
- flag icon -> FLAGS
- target/crosshair -> IGNORE
- other -> IGNORE unless the icon is clearly identified as one of the
  three real stats above.

Example non-CTF scoreboard:
target 6 | skull 17 | medal 29

Correct:
deaths = 17
kills = 29
flags = 0

NEVER:
deaths = 6
kills = 17
flags = 29

Example CTF scoreboard:
skull 14 | medal 17 | flag 2

Correct:
deaths = 14
kills = 17
flags = 2

IMPORTANT:
- Do NOT infer stat meaning from column position.
- Do NOT use the target number as deaths, kills, or flags.
- Do NOT treat a medal/crown number as flags.
- If there is NO flag icon, flags MUST be 0.
- The player stat values must come only from the icon->value mapping above.

WINNER RULES:

CTF / TEAM:
- Do NOT require "RED WINS" / "BLUE WINS" text.
- Read the two top-left team scores.
- If RED score == BLUE score:
    -> DRAW
    -> nobody gets a win
    -> nobody gets a loss
- If RED score != BLUE score:
    -> the team/color whose players appear FIRST/TOP in the main scoreboard
       is the winning team.
    -> every player of that color is a winner.
    -> every player of the other color is a loser.
- Score magnitude does NOT choose the winner.
- Preserve player order exactly.

FFA / NO TEAM:
- player(s) with the highest KILLS are winners.
- Highest-kill ties produce multiple winners.

Never invent players or scores.
"""




MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "is_smash_karts_scoreboard": {
            "type": "boolean",
            "description": "True only when this image is a final Smash Karts scoreboard/statistics screen."
        },
        "mode": {
            "type": "string",
            "enum": [
                "CTF",
                "FFA",
                "TEAM",
                "UNKNOWN"
            ]
        },
        "map": {
            "type": [
                "string",
                "null"
            ]
        },
        "winner_declared": {
            "type": "boolean"
        },
        "final_result_text": {
            "type": [
                "string",
                "null"
            ]
        },
        "winning_team": {
            "type": [
                "string",
                "null"
            ]
        },
        "team_score_red": {
            "type": [
                "integer",
                "null"
            ],
            "description": "Exact number in the RED-colored top-left score box."
        },
        "team_score_blue": {
            "type": [
                "integer",
                "null"
            ],
            "description": "Exact number in the BLUE-colored top-left score box."
        },
        "players": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string"
                    },
                    "team": {
                        "type": [
                            "string",
                            "null"
                        ]
                    },
                    "stat_columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "icon": {
                                    "type": "string",
                                    "enum": [
                                        "target",
                                        "skull",
                                        "medal",
                                        "flag",
                                        "other"
                                    ]
                                },
                                "value": {
                                    "type": "integer",
                                    "description": "Number directly below this icon."
                                }
                            },
                            "required": [
                                "icon",
                                "value"
                            ]
                        }
                    },
                    "is_winner": {
                        "type": "boolean"
                    }
                },
                "required": [
                    "name",
                    "team",
                    "stat_columns",
                    "is_winner"
                ]
            }
        }
    },
    "required": [
        "is_smash_karts_scoreboard",
        "mode",
        "map",
        "winner_declared",
        "final_result_text",
        "winning_team",
        "team_score_red",
        "team_score_blue",
        "players"
    ]
}


def get_telegram_file_bytes(file_id):

    result = telegram_request(
        "getFile",
        {
            "file_id": file_id
        }
    )

    if not result or not result.get("ok"):
        raise ValueError(
            "Telegram getFile failed"
        )

    file_path = (
        result["result"]
        .get("file_path")
    )

    if not file_path:
        raise ValueError(
            "Telegram did not return file_path"
        )

    url = (
        f"https://api.telegram.org/file/"
        f"bot{TELEGRAM_TOKEN}/"
        f"{file_path}"
    )

    request = urllib.request.Request(
        url
    )

    with urllib.request.urlopen(
        request,
        timeout=15
    ) as response:

        return response.read()


def analyze_match_image(
    image_bytes,
    mime_type="image/jpeg"
):

    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type=mime_type
    )

    response = (
        gemini_client
        .models
        .generate_content(
            model="gemini-3.1-flash-lite",
            contents=[
                image_part,
                MATCH_ANALYSIS_PROMPT
            ],
            config={
                "response_mime_type":
                    "application/json",

                "response_json_schema":
                    MATCH_SCHEMA,

                "thinking_config": {
                    "thinking_level":
                        "minimal"
                },

                "max_output_tokens": 1500,
            },
        )
    )

    text = extract_gemini_text(
        response
    )

    if text:
        print("RAW GEMINI MATCH:", text)

    if not text:
        raise ValueError(
            "Gemini returned no match analysis"
        )

    try:
        return json.loads(text)

    except json.JSONDecodeError as e:
        raise ValueError(
            f"Gemini returned invalid JSON: {e}"
        )



TEAM_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "team_score_red": {
            "type": "integer",
            "description": "Exact number in the RED-colored score box at the top-left."
        },
        "team_score_blue": {
            "type": "integer",
            "description": "Exact number in the BLUE-colored score box at the top-left."
        }
    },
    "required": [
        "team_score_red",
        "team_score_blue"
    ]
}


def recover_team_scores(image_bytes, mime_type="image/jpeg"):
    """
    Dedicated fallback when the main match extraction fails to return
    the two top-left team scores. This call reads ONLY the two colored
    score boxes and does not attempt to interpret winners or player stats.
    """
    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type=mime_type
    )

    prompt = """
Read ONLY the two team score boxes in the TOP-LEFT corner of this
Smash Karts screenshot.

Return JSON only.

The RED-colored box contains team_score_red.
The BLUE-colored box contains team_score_blue.

Ignore the player scoreboard completely.
Do not use player kills, deaths, flags, or ordering.
Do not use any winner text.

Example:
RED box = 2
BLUE box = 10

Return:
{
  "team_score_red": 2,
  "team_score_blue": 10
}
"""

    response = (
        gemini_client
        .models
        .generate_content(
            model="gemini-3.1-flash-lite",
            contents=[
                image_part,
                prompt
            ],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": TEAM_SCORE_SCHEMA,
                 "temperature": 0.9,
                "thinking_config": {
                    "thinking_level": "minimal"
                },
                "max_output_tokens": 100,
            },
        )
    )

    text = extract_gemini_text(response)

    if not text:
        raise ValueError(
            "Team score recovery returned no text"
        )

    recovered = json.loads(text)

    red = int(recovered["team_score_red"])
    blue = int(recovered["team_score_blue"])

    print(
        "RECOVERED TEAM SCORES:",
        json.dumps(
            {
                "red": red,
                "blue": blue,
            }
        )
    )

    return red, blue


def extract_player_stats_from_columns(stat_columns):
    """
    Convert icon-labeled scoreboard columns into the three database stats.

    Smash Karts can change the visual column order between scoreboard
    layouts. We therefore identify stats by their icon instead of position.

    target/crosshair -> ignored
    skull            -> deaths
    medal/crown      -> kills
    flag             -> flags
    """
    kills = 0
    deaths = 0
    flags = 0

    if not isinstance(stat_columns, list):
        return kills, deaths, flags

    for column in stat_columns:
        if not isinstance(column, dict):
            continue

        icon = str(
            column.get("icon") or "other"
        ).strip().lower()

        try:
            value = max(
                0,
                int(column.get("value") or 0)
            )
        except (TypeError, ValueError):
            continue

        if icon == "skull":
            deaths = value
        elif icon == "medal":
            kills = value
        elif icon == "flag":
            flags = value
        elif icon in {"target", "other"}:
            # Explicitly ignored.
            continue

    return kills, deaths, flags



def normalize_match_result(result):

    if not isinstance(
        result,
        dict
    ):
        raise ValueError(
            "Invalid match result"
        )

    mode = result.get(
        "mode",
        "UNKNOWN"
    )

    if mode not in {
        "CTF",
        "FFA",
        "TEAM",
        "UNKNOWN"
    }:
        mode = "UNKNOWN"

    players = result.get(
        "players",
        []
    )

    if (
        not isinstance(players, list)
        or not players
    ):
        raise ValueError(
            "No players found"
        )

    normalized_players = []

    for player in players:

        if not isinstance(
            player,
            dict
        ):
            continue

        name = str(
            player.get(
                "name",
                ""
            )
        ).strip()

        if not name:
            continue

        team = player.get(
            "team"
        )

        if team is not None:
            team = str(team).strip().upper()

            # Match RED/BLUE even if Gemini wraps it in extra words
            # ("RED TEAM", "TEAM RED", "🔴 RED", etc.) instead of only
            # matching the two exact strings -- the winner logic below
            # depends on every RED/BLUE player being recognized as such.
            if "RED" in team:
                team = "RED"
            elif "BLUE" in team:
                team = "BLUE"
            else:
                team = None

        # Read stats from their actual icons, never by fixed position.
        # Target/crosshair is ignored completely.
        kills, deaths, flags = extract_player_stats_from_columns(
            player.get("stat_columns", [])
        )
        normalized_players.append({
            "name": name,
            "team": team,
            "kills": kills,
            "deaths": deaths,
            "flags": flags,
            "is_winner": False,
        })

    if not normalized_players:
        raise ValueError(
            "No valid players found"
        )

    # Infer mode from player teams when Gemini cannot label it.
    if mode == "UNKNOWN":
        has_teams = any(
            player["team"] in {"RED", "BLUE"}
            for player in normalized_players
        )
        mode = "TEAM" if has_teams else "FFA"

    team_score_red = result.get("team_score_red")
    team_score_blue = result.get("team_score_blue")

    try:
        team_score_red = (
            int(team_score_red)
            if team_score_red is not None
            else None
        )
    except (TypeError, ValueError):
        team_score_red = None

    try:
        team_score_blue = (
            int(team_score_blue)
            if team_score_blue is not None
            else None
        )
    except (TypeError, ValueError):
        team_score_blue = None

    final_result_text = result.get(
        "final_result_text"
    )

    is_draw = False
    winner_declared = False
    winning_team = None

    # ========================================================
    # NEW WINNER RULES
    # ========================================================

    if mode in {"CTF", "TEAM"}:

        # A team match is recordable only when both top-left scores
        # are available.
        if (
            team_score_red is not None
            and team_score_blue is not None
        ):

            winner_declared = True

            if team_score_red == team_score_blue:

                # DRAW
                is_draw = True
                winning_team = "DRAW"

                for player in normalized_players:
                    player["is_winner"] = False

            else:

                # Unequal score:
                # winner is the color whose players appear FIRST in the
                # main scoreboard.
                first_team = next(
                    (
                        player["team"]
                        for player in normalized_players
                        if player["team"] in {"RED", "BLUE"}
                    ),
                    None,
                )

                if first_team in {"RED", "BLUE"}:

                    winning_team = first_team

                    for player in normalized_players:
                        player["is_winner"] = (
                            player["team"] == winning_team
                        )

                else:
                    # Cannot safely determine scoreboard order/color.
                    # Still record the scoreboard, but do not assign a winner.
                    winner_declared = True
                    winning_team = None

                    for player in normalized_players:
                        player["is_winner"] = False

        else:

            # Missing one of the top-left scores. Still record the match;
            # simply do not assign a team winner.
            winner_declared = True
            winning_team = None

            for player in normalized_players:
                player["is_winner"] = False

    elif mode == "FFA":

        # For FFA, highest kills are the winners.
        # Multiple players can tie.
        winner_declared = True

        highest_kills = max(
            player["kills"]
            for player in normalized_players
        )

        for player in normalized_players:
            player["is_winner"] = (
                player["kills"] == highest_kills
            )

    else:

        winner_declared = True

        for player in normalized_players:
            player["is_winner"] = False

        winning_team = None

    # Keep explicit visible result text for information only. It is never
    # required and never used to determine a winner -- Python's rules
    # above (scores + scoreboard order, or highest kills) are authoritative.
    if not isinstance(final_result_text, str):
        final_result_text = None

    return {
        "mode": mode,
        "map": result.get("map"),
        "winner_declared": winner_declared,
        "is_draw": is_draw,
        "final_result_text": final_result_text,
        "winning_team": winning_team,
        "team_score_red": team_score_red,
        "team_score_blue": team_score_blue,
        "players": normalized_players,
    }



def save_match(
    chat_id,
    telegram_message_id,
    played_at,
    match_data
):
    """
    Records the match through the atomic record_match() Postgres RPC.

    Python has already mapped the scoreboard icons to kills/deaths/flags.
    The database stores the payload as-is; there is no database-side
    kills/deaths transformation.
    """

    players_payload = [
        {
            "name": player["name"],
            "team": player["team"],
            "kills": player["kills"],
            "deaths": player["deaths"],
            "flags": player["flags"],
            "is_winner": player["is_winner"],
        }
        for player in match_data["players"]
    ]

    print(
        "FINAL DB PAYLOAD:",
        json.dumps(players_payload, indent=2)
    )

    print(
        "MATCH RESULT:",
        json.dumps(
            {
                "mode": match_data["mode"],
                "winning_team": match_data["winning_team"],
                "is_draw": match_data["is_draw"],
                "team_score_red": match_data["team_score_red"],
                "team_score_blue": match_data["team_score_blue"],
            },
            indent=2
        )
    )

    result = (
        supabase
        .rpc(
            "record_match",
            {
                "p_chat_id": chat_id,
                "p_telegram_message_id": telegram_message_id,
                "p_mode": match_data["mode"],
                "p_map": match_data["map"],
                "p_winning_team": match_data["winning_team"],
                "p_team_score_red": match_data["team_score_red"],
                "p_team_score_blue": match_data["team_score_blue"],
                "p_played_at": played_at,
                "p_players": players_payload,
            },
        )
        .execute()
    )

    if result.data is None:
        print(
            "Match already stored:",
            telegram_message_id
        )
        return False

    print(
        "Match saved via record_match:",
        result.data
    )
    return True



def analyze_and_save_match(
    image_bytes,
    mime_type,
    chat_id,
    telegram_message_id,
    telegram_timestamp
):

    raw_result = analyze_match_image(
        image_bytes,
        mime_type
    )

    if not bool(raw_result.get("is_smash_karts_scoreboard")):
        print("IMAGE IS NOT A SMASH KARTS SCOREBOARD")
        return False, None

    # If the main vision call recognized a team match but failed to return
    # the two top-left scores, make a dedicated second vision call that
    # reads ONLY those two boxes.
    #
    # Also recover team mode from RED/BLUE player colors when Gemini labels
    # the mode as UNKNOWN.
    raw_players = raw_result.get("players", [])

    has_red_blue_players = any(
        isinstance(player, dict)
        and str(player.get("team") or "").strip().upper()
        in {"RED", "BLUE"}
        for player in raw_players
    )

    raw_mode = str(
        raw_result.get("mode") or "UNKNOWN"
    ).upper()

    if raw_mode == "UNKNOWN" and has_red_blue_players:
        raw_result["mode"] = "TEAM"
        raw_mode = "TEAM"

    if raw_mode in {"TEAM", "CTF"}:
        raw_red = raw_result.get("team_score_red")
        raw_blue = raw_result.get("team_score_blue")

        if raw_red is None or raw_blue is None:
            try:
                recovered_red, recovered_blue = recover_team_scores(
                    image_bytes,
                    mime_type
                )
                raw_result["team_score_red"] = recovered_red
                raw_result["team_score_blue"] = recovered_blue
            except Exception as e:
                print(
                    "TEAM SCORE RECOVERY FAILED:",
                    repr(e)
                )

    match_data = normalize_match_result(
        raw_result
    )
    if not telegram_timestamp:
        raise ValueError(
            "Telegram timestamp missing"
        )

    played_at = (
        datetime
        .fromtimestamp(
            telegram_timestamp,
            tz=timezone.utc
        )
        .isoformat()
    )

    saved = save_match(
        chat_id,
        telegram_message_id,
        played_at,
        match_data
    )

    return (
        saved,
        match_data
    )



def period_bounds(period):
    now = datetime.now(IST)
    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    if period == "week":
        start = start.replace(
            day=start.day
        )
        start = start - timedelta(days=start.weekday())
    elif period == "month":
        start = start.replace(day=1)

    if period == "today":
        end = start + timedelta(days=1)
    elif period == "week":
        end = start + timedelta(days=7)
    else:
        if start.month == 12:
            next_month = start.replace(
                year=start.year + 1,
                month=1,
                day=1,
            )
        else:
            next_month = start.replace(
                month=start.month + 1,
                day=1,
            )
        end = next_month

    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


DEFAULT_MATCH_HISTORY_LIMIT = 1000
MATCH_PLAYERS_CHUNK_SIZE = 200


def get_match_history(period=None, limit=None):
    """
    Fetches matches (optionally scoped to a period) and their players.

    Always caps the number of matches fetched -- the old version had
    no default limit, so the "no period, no limit" call used by the
    leaderboard fallback and by admin resets could pull the entire
    match history (and then every match_players row for it) into
    memory on every invocation. It also fetches match_players in
    chunks instead of one `.in_(all_match_ids)` call, so a large
    history doesn't build a single filter long enough to hit
    PostgREST's URL/query-length limits.
    """

    query = (
        supabase
        .table("matches")
        .select(
            "id,mode,map,winning_team,team_score_red,"
            "team_score_blue,played_at,telegram_message_id"
        )
        .order("played_at", desc=True)
    )

    if period:
        start, end = period_bounds(period)
        query = query.gte("played_at", start).lt("played_at", end)

    query = query.limit(limit or DEFAULT_MATCH_HISTORY_LIMIT)

    matches = query.execute().data or []

    if not matches:
        return [], []

    match_ids = [match["id"] for match in matches]

    players = []

    for i in range(0, len(match_ids), MATCH_PLAYERS_CHUNK_SIZE):
        chunk = match_ids[i:i + MATCH_PLAYERS_CHUNK_SIZE]

        chunk_players = (
            supabase
            .table("match_players")
            .select(
                "match_id,player_name,team,kills,deaths,flags,is_winner"
            )
            .in_("match_id", chunk)
            .execute()
            .data
            or []
        )

        # Preserve draw information at the match level without changing
        # the database schema of match_players.
        draw_match_ids = {
            match["id"]
            for match in matches
            if str(match.get("winning_team") or "").upper() == "DRAW"
        }

        for player in chunk_players:
            player["is_draw"] = (
                player.get("match_id") in draw_match_ids
            )

        players.extend(chunk_players)

    return matches, players


def aggregate_player_stats(players):
    totals = {}

    for player in players:
        name = player.get("player_name")
        if not name:
            continue

        stats = totals.setdefault(
            name,
            {
                "wins": 0,
                "losses": 0,
                "kills": 0,
                "deaths": 0,
                "flags": 0,
                "draws": 0,
                "matches": 0,
            },
        )
        is_draw = bool(player.get("is_draw"))

        stats["wins"] += 1 if player.get("is_winner") else 0
        stats["losses"] += (
            0
            if player.get("is_winner") or is_draw
            else 1
        )
        stats["draws"] = stats.get("draws", 0) + (
            1 if is_draw else 0
        )
        stats["kills"] += int(player.get("kills") or 0)
        stats["deaths"] += int(player.get("deaths") or 0)
        stats["flags"] += int(player.get("flags") or 0)
        stats["matches"] += 1

    return totals


def format_kd(kills, deaths):
    if not deaths:
        return "∞" if kills else "0.00"
    return f"{kills / deaths:.2f}"


def format_leaderboard(rows, title="🏆 Leaderboard"):
    has_stats = any(
        stats["wins"]
        or stats["losses"]
        or stats["kills"]
        or stats["deaths"]
        or stats["flags"]
        for _, stats in rows
    )

    if not rows or not has_stats:
        return (
            f"{title}\n"
            "━━━━━━━━━━━━━━\n"
            "No recorded stats yet 🧹"
        )

    lines = [
        title,
        "━━━━━━━━━━━━━━",
    ]

    rank_icons = ["🥇", "🥈", "🥉"]
    for index, (name, stats) in enumerate(rows, start=1):
        rank = (
            rank_icons[index - 1]
            if index <= len(rank_icons)
            else f"{index}."
        )
        lines.extend([
            f"{rank} {name}",
            f"   {stats['wins']}W · {stats['losses']}L   |   "
            f"{stats['kills']}K · {stats['deaths']}D   |   "
            f"KD {format_kd(stats['kills'], stats['deaths'])}   |   "
            f"🚩 {stats['flags']} flags",
        ])

    return "\n".join(lines)


def leaderboard_rows(period=None):
    if period:
        _, players = get_match_history(period)
        totals = aggregate_player_stats(players)
    else:
        result = (
            supabase
            .table("players")
            .select("player_name,wins,losses,kills,deaths,flags")
            .execute()
        )
        rows = result.data or []

        if rows:
            totals = {
                row["player_name"]: {
                    "wins": int(row.get("wins") or 0),
                    "losses": int(row.get("losses") or 0),
                    "kills": int(row.get("kills") or 0),
                    "deaths": int(row.get("deaths") or 0),
                    "flags": int(row.get("flags") or 0),
                    "matches": 0,
                }
                for row in rows
            }
        else:
            _, players = get_match_history()
            totals = aggregate_player_stats(players)

    return sorted(
        totals.items(),
        key=lambda item: (
            item[1]["wins"],
            item[1]["kills"],
            -item[1]["deaths"],
        ),
        reverse=True,
    )


def parse_create_room_command(prompt: str):
    """Parse a flexible Smash Karts room command."""
    text = " ".join(prompt.strip().split())
    if not re.match(r"^create\s+room\b", text, re.IGNORECASE):
        return None

    rest = re.sub(r"^create\s+room\b", "", text, flags=re.IGNORECASE).strip()

    # Optional players clause anywhere in the command.
    max_players = None
    players_match = re.search(
        r"\bplayers?\s*(?:=|:)?\s*(\d+)\b",
        rest,
        re.IGNORECASE,
    )
    if players_match:
        max_players = int(players_match.group(1))
        rest = (
            rest[:players_match.start()] + " " + rest[players_match.end():]
        ).strip()

    # Optional FFA teams clause.
    teams = bool(re.search(r"\bteams?\b", rest, re.IGNORECASE))
    if teams:
        rest = re.sub(r"\bteams?\b", " ", rest, flags=re.IGNORECASE).strip()

    # Optional weapon clause.
    disabled_weapons = []
    weapon_match = re.search(
        r"\b(?:without|disable|disabled?)\s+(.+)$",
        rest,
        re.IGNORECASE,
    )
    if weapon_match:
        weapon_text = weapon_match.group(1).strip()
        rest = rest[:weapon_match.start()].strip()
        disabled_weapons = [
            item.strip()
            for item in re.split(r"\s*,\s*|\s+and\s+", weapon_text, flags=re.IGNORECASE)
            if item.strip()
        ]

    tokens = rest.split()
    game_mode = "ffa"
    round_format = "timer"
    value = 10

    if tokens and tokens[0].lower() in {"ffa", "ctf"}:
        game_mode = tokens.pop(0).lower()

    if tokens and tokens[0].lower() in {
        "timer",
        "score",
        "score-target",
        "score_target",
    }:
        round_format = tokens.pop(0).lower()
        if not tokens:
            return {"error": "Please provide the timer/score value."}
        try:
            value = int(tokens.pop(0))
        except ValueError:
            return {"error": "Timer/score value must be a number."}
    elif tokens and re.fullmatch(r"\d+", tokens[0]):
        value = int(tokens.pop(0))

    arena = " ".join(tokens).strip()
    if not arena:
        arena = "smash island"

    return {
        "game_mode": game_mode,
        "round_format": round_format,
        "value": value,
        "arena": arena,
        "max_players": max_players,
        "teams": teams,
        "disabled_weapons": disabled_weapons,
    }


def send_public_stats_command(prompt, chat_id, message_id):
    lower = prompt.strip().lower()

    if lower == "show commands":
        send_message(
            chat_id,
            "📖 Commands\n"
            "━━━━━━━━━━━━━━\n"
            "• leaderboard\n"
            "• leaderboard today\n"
            "• leaderboard week\n"
            "• leaderboard month\n"
            "• stats <player>\n"
            "• most kills today\n"
            "• most deaths today\n"
            "• most flags today\n"
            "• most today\n"
            "• best kd today\n"
            "• most active player\n"
            "• remember <text>\n"
            "• forget <text>\n"
            "• what do you remember about me\n"
            "• random map / choose a map / pick a map\n"
            "• create room ffa timer 10 DZ players 12\n"
            "• create room ctf timer 10 LP players 8\n\n"
            "🖼 Send a scoreboard screenshot with "
            "@urrsbro_bot in the caption.",
            message_id,
        )
        return True

    if lower == "show admin commands":
        send_message(
            chat_id,
            "🔒 Admin commands\n"
            "━━━━━━━━━━━━━━\n"
            "• add player <name>\n"
            "• remove player <name>\n"
            "• reset player <name>\n"
            "• delete match <id>\n"
            "• reset leaderboard\n"
            "• reset matches",
            message_id,
        )
        return True

    if lower == "leaderboard" or lower.startswith("leaderboard "):
        period_name = lower.removeprefix("leaderboard").strip()
        period = period_name if period_name in {
            "today", "week", "month"
        } else None
        title = "🏆 Leaderboard"
        if period:
            title += f" ({period})"
        send_message(
            chat_id,
            format_leaderboard(leaderboard_rows(period), title),
            message_id,
        )
        return True

    stats_match = re.match(r"^stats\s+(.+)$", prompt, re.IGNORECASE)
    if stats_match:
        player_name = stats_match.group(1).strip()

        player_result = (
            supabase
            .table("players")
            .select(
                "player_name,wins,losses,kills,deaths,flags"
            )
            .execute()
        )

        player_rows = player_result.data or []

        if player_rows:
            totals = {
                row["player_name"]: {
                    "wins": int(row.get("wins") or 0),
                    "losses": int(row.get("losses") or 0),
                    "kills": int(row.get("kills") or 0),
                    "deaths": int(row.get("deaths") or 0),
                    "flags": int(row.get("flags") or 0),
                    "matches": (
                        int(row.get("wins") or 0)
                        + int(row.get("losses") or 0)
                    ),
                }
                for row in player_rows
            }
        else:
            _, history_players = get_match_history()
            totals = aggregate_player_stats(history_players)

        search_name = player_name.casefold()

        exact_matches = [
            name
            for name in totals
            if name.casefold() == search_name
        ]

        partial_matches = [
            name
            for name in totals
            if search_name in name.casefold()
        ]

        matches = exact_matches or partial_matches

        if len(matches) > 1:
            send_message(
                chat_id,
                "Multiple players matched: "
                + ", ".join(sorted(matches)),
                message_id
            )

        elif not matches:
            send_message(
                chat_id,
                "Player not found 🤷",
                message_id
            )

        else:
            matched_name = matches[0]
            row = totals[matched_name]

            wins = int(row.get("wins") or 0)
            losses = int(row.get("losses") or 0)
            kills = int(row.get("kills") or 0)
            deaths = int(row.get("deaths") or 0)
            flags = int(row.get("flags") or 0)

            # Count draws from match history because the cumulative
            # players table intentionally stores only wins/losses.
            draws = 0
            try:
                _, history_players = get_match_history()
                draws = sum(
                    1
                    for item in history_players
                    if (
                        item.get("player_name", "").casefold()
                        == matched_name.casefold()
                        and item.get("is_draw")
                    )
                )
            except Exception as e:
                print("STATS DRAW COUNT ERROR:", repr(e))

            matches_played = wins + losses + draws

            if matches_played:
                win_rate = (
                    wins / matches_played
                ) * 100
            else:
                win_rate = 0.0

            kd = format_kd(
                kills,
                deaths
            )

            stats_text = (
                f"📊 {matched_name}\n"
                "━━━━━━━━━━━━━━\n"
                f"🎮 Matches: {matches_played}\n"
                f"🏆 Wins: {wins}\n"
                f"💀 Losses: {losses}\n"
                f"🤝 Draws: {draws}\n"
                f"📈 Win Rate: {win_rate:.1f}%\n\n"
                f"⚔️ Kills: {kills}\n"
                f"💀 Deaths: {deaths}\n"
                f"🎯 KD: {kd}\n"
                f"🚩 Flags: {flags}"
            )

            send_message(
                chat_id,
                stats_text,
                message_id
            )

        return True

    if lower == "recent matches":
        matches, _ = get_match_history(limit=10)
        if not matches:
            reply = "🕘 Recent matches\nNo matches yet 😭"
        else:
            lines = ["🕘 Recent matches"]
            for match in matches:
                lines.append(
                    f"#{match['id']} — {match.get('mode') or 'UNKNOWN'} — "
                    f"{match.get('map') or 'Unknown map'}"
                )
            reply = "\n".join(lines)
        send_message(chat_id, reply, message_id)
        return True

    match_command = re.match(r"^match\s+(\d+)$", prompt, re.IGNORECASE)
    if match_command:
        match_id = int(match_command.group(1))
        matches, players = get_match_history()
        match = next((item for item in matches if item["id"] == match_id), None)
        if not match:
            send_message(chat_id, "Match not found 🤷", message_id)
            return True
        match_players = [item for item in players if item["match_id"] == match_id]
        winners = [item["player_name"] for item in match_players if item.get("is_winner")]
        send_message(
            chat_id,
            f"🏁 Match #{match_id} — {match.get('mode') or 'UNKNOWN'}\n"
            f"Map: {match.get('map') or 'Unknown'}\n"
            f"Winner: {', '.join(winners) or 'Result unclear'}",
            message_id,
        )
        return True

    metric_commands = {
        "most kills today": ("kills", "Most kills today"),
        "most deaths today": ("deaths", "Most deaths today"),
        "most flags today": ("flags", "Most flags today"),
        "most today": ("wins", "Most wins today"),
    }
    if lower in metric_commands:
        metric, label = metric_commands[lower]
        totals = aggregate_player_stats(get_match_history("today")[1])
        winner = max(totals.items(), key=lambda item: item[1][metric], default=None)
        if not winner or winner[1][metric] == 0:
            reply = f"📈 {label}\nNo stats yet 😭"
        else:
            reply = f"📈 {label}: {winner[0]} — {winner[1][metric]}"
        send_message(chat_id, reply, message_id)
        return True

    if lower == "best kd today":
        totals = aggregate_player_stats(get_match_history("today")[1])
        valid = [item for item in totals.items() if item[1]["deaths"]]
        winner = max(valid, key=lambda item: item[1]["kills"] / item[1]["deaths"], default=None)
        reply = (
            f"🎯 Best KD today: {winner[0]} — "
            f"{format_kd(winner[1]['kills'], winner[1]['deaths'])}"
            if winner else "🎯 Best KD today\nNo stats yet 😭"
        )
        send_message(chat_id, reply, message_id)
        return True

    if lower == "most active player":
        totals = aggregate_player_stats(get_match_history()[1])
        winner = max(totals.items(), key=lambda item: item[1]["matches"], default=None)
        reply = (
            f"🔥 Most active player: {winner[0]} — "
            f"{winner[1]['matches']} matches"
            if winner else "🔥 Most active player\nNo matches yet 😭"
        )
        send_message(chat_id, reply, message_id)
        return True
    room_command = parse_create_room_command(prompt)
    if room_command is not None:
        if room_command.get("error"):
            send_message(chat_id, room_command["error"], message_id)
            return True

        try:
            kwargs = {
                "round_format": room_command["round_format"],
                "value": room_command["value"],
                "arena": room_command["arena"],
                "weapons": disable_weapons(
                    room_command.get("disabled_weapons", [])
                ),
            }

            if room_command["game_mode"] == "ctf":
                kwargs["max_players"] = (
                    room_command["max_players"]
                    if room_command["max_players"] is not None
                    else 8
                )
                url = create_ctf_room_url(**kwargs)
            else:
                kwargs["max_players"] = (
                    room_command["max_players"]
                    if room_command["max_players"] is not None
                    else 24
                )
                kwargs["teams"] = bool(room_command.get("teams", False))
                url = create_ffa_room_url(**kwargs)

            send_message(
                chat_id,
                "🏁 Come play Smash Karts\n"
                f"Room: {url.split('room=')[1].split('&')[0]}\n"
                f"{url}",
                message_id,
            )
        except ValueError as e:
            send_message(chat_id, f"Room error: {e}", message_id)
        except Exception as e:
            print("CREATE ROOM ERROR:", repr(e))
            send_message(chat_id, "Couldn't create that room 💀", message_id)

        return True
    return False

# ============================================================
# ADMIN / DATABASE COMMANDS
# ============================================================


def is_admin(user_id):
    return user_id in ADMIN_IDS


def normalize_name(name):
    return name.strip()


def add_player(player_name):
    player_name = normalize_name(player_name)

    result = (
        supabase
        .table("players")
        .upsert(
            {
                "player_name": player_name
            },
            on_conflict="player_name"
        )
        .execute()
    )

    return bool(result.data)


def remove_player(player_name):
    player_name = normalize_name(player_name)

    result = (
        supabase
        .table("players")
        .delete()
        .eq("player_name", player_name)
        .execute()
    )

    return bool(result.data)


def reset_player(player_name):
    player_name = normalize_name(player_name)

    result = (
        supabase
        .table("players")
        .update(
            {
                "wins": 0,
                "losses": 0,
                "kills": 0,
                "deaths": 0,
                "flags": 0
            }
        )
        .eq("player_name", player_name)
        .execute()
    )

    return bool(result.data)


def delete_match(match_id):
    """
    Deletes a match and reverses its leaderboard contribution in a
    single atomic call to delete_match_and_adjust_totals() (see
    schema_fixes.sql).

    This replaces ~80 lines of Python that fetched each affected
    player's row one at a time, computed the new totals by hand,
    updated them one at a time, and tried to manually snapshot and
    roll back on failure. That version had the same lost-update race
    as update_player_totals, plus N+1 queries, plus hand-rolled
    rollback logic that itself could fail partway through. The SQL
    function runs as one transaction: it either fully applies or
    fully rolls back on its own, with no Python-side bookkeeping.
    """

    result = (
        supabase
        .rpc(
            "delete_match_and_adjust_totals",
            {"p_match_id": match_id},
        )
        .execute()
    )

    return bool(result.data)


def reset_leaderboard():
    """
    Zeroes cumulative leaderboard totals only; match history is left
    intact (period-based lookups like "leaderboard today" always
    recompute straight from match_players, not from this cache, so
    they're unaffected).

    The old version first re-fetched the *entire* match history just
    to reseed player names one-by-one before zeroing -- unnecessary,
    since every match participant already has a `players` row
    (record_match upserts one for every player in every match). This
    is now a single UPDATE.
    """

    result = (
        supabase
        .table("players")
        .update(
            {
                "wins": 0,
                "losses": 0,
                "kills": 0,
                "deaths": 0,
                "flags": 0
            }
        )
        .neq(
            "player_name",
            ""
        )
        .execute()
    )

    return bool(result.data)


def reset_matches():
    """
    Wipes match history and leaderboard totals atomically via
    reset_all_matches_and_totals() (see schema_fixes.sql), instead of
    three separate, non-transactional delete/update calls that could
    leave the database half-cleared if one of them failed partway
    through.
    """

    try:
        supabase.rpc("reset_all_matches_and_totals").execute()
        return True
    except Exception as rpc_error:
        print(
            "RESET MATCHES RPC ERROR; using guarded fallback:",
            repr(rpc_error),
        )

    # The fallback keeps every DELETE explicitly scoped. This also supports
    # databases where the RPC was created before the DELETE safety fix.
    (
        supabase
        .table("match_players")
        .delete()
        .neq("id", 0)
        .execute()
    )

    (
        supabase
        .table("matches")
        .delete()
        .neq("id", 0)
        .execute()
    )

    (
        supabase
        .table("players")
        .update({
            "wins": 0,
            "losses": 0,
            "kills": 0,
            "deaths": 0,
            "flags": 0,
        })
        .neq("player_name", "")
        .execute()
    )

    return True

def handle_database_command(
    prompt,
    chat_id,
    user_id,
    message_id
):
    """
    Returns True if this was a database command.
    """

    p = prompt.strip()

    lower = p.lower()

    # --------------------------------------------------------
    # ADD PLAYER
    # --------------------------------------------------------

    match = re.match(
        r"^add\s+player\s+(.+)$",
        p,
        flags=re.IGNORECASE
    )

    if match:

        if not is_admin(user_id):
            send_message(
                chat_id,
                "Nah bro 😭 admin only.",
                message_id
            )
            return True

        name = match.group(1).strip()

        try:

            if add_player(name):

                send_message(
                    chat_id,
                    f"Added {name} ✅",
                    message_id
                )

            else:

                send_message(
                    chat_id,
                    "Couldn't add player 💀",
                    message_id
                )

        except Exception as e:

            print(
                "ADD PLAYER ERROR:",
                repr(e)
            )

            send_message(
                chat_id,
                "Database said nah 💀",
                message_id
            )

        return True


    # --------------------------------------------------------
    # REMOVE PLAYER
    # --------------------------------------------------------

    match = re.match(
        r"^remove\s+player\s+(.+)$",
        p,
        flags=re.IGNORECASE
    )

    if match:

        if not is_admin(user_id):
            send_message(
                chat_id,
                "Nah bro 😭 admin only.",
                message_id
            )
            return True

        name = match.group(1).strip()

        try:

            if remove_player(name):

                send_message(
                    chat_id,
                    f"Removed {name} 🗑️",
                    message_id
                )

            else:

                send_message(
                    chat_id,
                    "Player not found 🤷",
                    message_id
                )

        except Exception as e:

            print(
                "REMOVE PLAYER ERROR:",
                repr(e)
            )

            send_message(
                chat_id,
                "Database error 💀",
                message_id
            )

        return True


    # --------------------------------------------------------
    # RESET PLAYER
    # --------------------------------------------------------

    match = re.match(
        r"^reset\s+player\s+(.+)$",
        p,
        flags=re.IGNORECASE
    )

    if match:

        if not is_admin(user_id):
            send_message(
                chat_id,
                "Nah bro 😭 admin only.",
                message_id
            )
            return True

        name = match.group(1).strip()

        try:

            if reset_player(name):

                send_message(
                    chat_id,
                    f"{name} reset to 0 🧹",
                    message_id
                )

            else:

                send_message(
                    chat_id,
                    "Player not found 🤷",
                    message_id
                )

        except Exception as e:

            print(
                "RESET PLAYER ERROR:",
                repr(e)
            )

            send_message(
                chat_id,
                "Database error 💀",
                message_id
            )

        return True


    # --------------------------------------------------------
    # DELETE MATCH
    # --------------------------------------------------------

    match = re.match(
        r"^delete\s+match\s+(\d+)$",
        p,
        flags=re.IGNORECASE
    )

    if match:

        if not is_admin(user_id):
            send_message(
                chat_id,
                "Nah bro 😭 admin only.",
                message_id
            )
            return True

        match_id = int(
            match.group(1)
        )

        try:

            if delete_match(match_id):

                send_message(
                    chat_id,
                    f"Match #{match_id} deleted 🗑️",
                    message_id
                )

            else:

                send_message(
                    chat_id,
                    "Match not found 🤷",
                    message_id
                )

        except Exception as e:

            print(
                "DELETE MATCH ERROR:",
                repr(e)
            )

            send_message(
                chat_id,
                "Database error 💀",
                message_id
            )

        return True


    # --------------------------------------------------------
    # RESET LEADERBOARD
    # --------------------------------------------------------

    if lower == "reset leaderboard":

        if not is_admin(user_id):
            send_message(
                chat_id,
                "Nah bro 😭 admin only.",
                message_id
            )
            return True

        try:

            reset_leaderboard()

            send_message(
                chat_id,
                "Leaderboard reset 🧹",
                message_id
            )

        except Exception as e:

            print(
                "RESET LEADERBOARD ERROR:",
                repr(e)
            )

            send_message(
                chat_id,
                "Database error 💀",
                message_id
            )

        return True


    # --------------------------------------------------------
    # RESET MATCHES
    # --------------------------------------------------------

    if lower == "reset matches":

        if not is_admin(user_id):
            send_message(
                chat_id,
                "Nah bro 😭 admin only.",
                message_id
            )
            return True

        try:

            reset_matches()

            send_message(
                chat_id,
                "All match history deleted 💀",
                message_id
            )

        except Exception as e:

            print(
                "RESET MATCHES ERROR:",
                repr(e)
            )

            send_message(
                chat_id,
                "Database error 💀",
                message_id
            )

        return True


    return False

# ============================================================
# PROCESS UPDATE
# ============================================================

def process_update(update):

    initialize_clients()

    message = update.get(
        "message"
    )

    if not message:
        return

    # ========================================================
    # PHOTO / SCREENSHOT
    # ========================================================

    photos = message.get("photo")

    if photos:
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        telegram_timestamp = message.get("date")
        chat_type = str(chat.get("type") or "")
        caption = message.get("caption", "") or ""
        mention = f"@{BOT_USERNAME}"
        mentioned = mention.lower() in caption.lower()

        if not chat_id:
            return

        # In groups, every photo is automatically checked.
        # Outside groups, keep the old explicit-mention behavior.
        auto_check = chat_type in {"group", "supergroup"}
        if not auto_check and not mentioned:
            return

        try:
            print("Checking uploaded image for Smash Karts scoreboard...")

            if mentioned:
                send_typing(chat_id)

            largest_photo = photos[-1]
            file_id = largest_photo.get("file_id")
            if not file_id:
                raise ValueError("Photo file_id missing")

            image_bytes = get_telegram_file_bytes(file_id)

            saved, match_data = analyze_and_save_match(
                image_bytes,
                "image/jpeg",
                chat_id,
                message_id,
                telegram_timestamp,
            )

            # Normal group photos are silent unless a Smash Karts scoreboard
            # was detected and the user explicitly mentioned Buddy.
            if match_data is None:
                if mentioned:
                    send_message(chat_id, "That isn't a Smash Karts scoreboard 😭", message_id)
                return

            if not saved:
                if mentioned:
                    send_message(chat_id, "Already recorded this match 😎", message_id)
                return

            if not mentioned:
                # Automatic recording: save silently. No leaderboard spam.
                print("AUTO-RECORDED SMASH KARTS MATCH", message_id)
                return

            players = match_data["players"]
            winners = [player["name"] for player in players if player["is_winner"]]
            map_name = match_data["map"]

            if match_data.get("is_draw"):
                reply = "Recorded 🤝\nResult: Draw"
            else:
                winner_text = ", ".join(winners) if winners else "Result unclear"
                reply = f"Recorded 🏆\nWinner: {winner_text}"

            if map_name:
                reply += f"\nMap: {map_name}"

            send_message(chat_id, reply, message_id)

        except Exception as e:
            print("MATCH SCREENSHOT ERROR:", repr(e))
            if mentioned:
                send_message(chat_id, "Couldn't read that scoreboard 💀", message_id)

        return


    # ========================================================
    # TEXT MESSAGE
    # ========================================================

    text = message.get(
        "text"
    )

    if not text:
        return

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    message_id = message.get(
        "message_id"
    )

    if not chat_id:
        return


    # ========================================================
    # USER
    # ========================================================

    sender = message.get(
        "from",
        {}
    )

    user_id = sender.get(
        "id"
    )

    username = sender.get(
        "username"
    )

    if not user_id:
        return

    chat_type = str(chat.get("type") or "")
    if chat_type in {"group", "supergroup"}:
        save_chat_message(
            chat_id,
            message_id,
            user_id,
            username,
            text,
            message.get("date"),
        )


    # ========================================================
    # MENTION
    # ========================================================

    mention = f"@{BOT_USERNAME}"

    mentioned = (
        mention.lower()
        in text.lower()
    )


    # ========================================================
    # REPLY TO BUDDY
    # ========================================================

    reply_to = message.get(
        "reply_to_message"
    )

    replying_to_buddy = False
    previous_bot_message = None
    reply_context = None

    if reply_to:

        # The replied-to message matters even when it was sent by another user.
        # This lets commands like "@urrsbro_bot ans" answer the quoted message.
        reply_context = (
            reply_to.get("text")
            or reply_to.get("caption")
            or ""
        ).strip()

        reply_sender = reply_to.get(
            "from",
            {}
        )

        reply_username = (
            reply_sender.get(
                "username"
            )
        )

        reply_is_bot = (
            reply_sender.get(
                "is_bot",
                False
            )
        )

        if (
            reply_username
            and reply_username.lower()
            == BOT_USERNAME.lower()
        ):

            replying_to_buddy = True

            previous_bot_message = (
                reply_to.get(
                    "text",
                    ""
                )
            )

        elif reply_is_bot:

            replying_to_buddy = True

            previous_bot_message = (
                reply_to.get(
                    "text",
                    ""
                )
            )


    # ========================================================
    # NORMAL GROUP CHAT PARTICIPATION
    # ========================================================

    if (
        not mentioned
        and not replying_to_buddy
    ):
        if str(chat.get("type") or "") in {"group", "supergroup"}:
            recent_rows = get_recent_chat_context(chat_id)
            maybe_participate_in_chat(
                chat_id,
                username,
                text,
                recent_rows,
                message_id,
            )
        return


    # ========================================================
    # REMOVE MENTION
    # ========================================================

    prompt = re.sub(
        re.escape(mention),
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    print(
        "Question:",
        prompt
    )


    # ========================================================
    # EMPTY PROMPT
    # ========================================================

    if not prompt:

        if reply_context:
            prompt = "Answer the Telegram message being replied to."
        else:
            send_message(
                chat_id,
                "Kya hai...",
                message_id,
            )
            return

    if prompt.casefold() in {
        "show commands",
        "show admin commands",
    }:
        send_public_stats_command(
            prompt,
            chat_id,
            message_id,
        )
        return

    continue_text_flow(
        prompt,
        chat_id,
        user_id,
        message_id,
        username,
        previous_bot_message,
        reply_context,
        replying_to_buddy,
    )

def continue_text_flow(
    prompt,
    chat_id,
    user_id,
    message_id,
    username,
    previous_bot_message,
    reply_context,
    replying_to_buddy,
):
    # ========================================================
    # DATABASE COMMANDS
    # ========================================================

    try:
        if send_public_stats_command(
            prompt,
            chat_id,
            message_id,
        ):
            return
    except Exception as e:
        print("PUBLIC DATABASE COMMAND ERROR:", repr(e))
        send_message(
            chat_id,
            "Database error 💀",
            message_id,
        )
        return

    if handle_database_command(
        prompt,
        chat_id,
        user_id,
        message_id
    ):
        return

    # ========================================================
    # REMEMBER
    # ========================================================

    remember_text = (
        extract_remember_text(
            prompt
        )
    )

    if remember_text:

        status = save_memory(
            chat_id,
            user_id,
            username,
            remember_text,
        )

        if status == "saved":

            send_message(
                chat_id,
                "Got it, I'll remember that 🧠",
                message_id,
            )

        elif status == "duplicate":

            send_message(
                chat_id,
                "Already got that 😎",
                message_id,
            )

        else:

            send_message(
                chat_id,
                "Memory system is cooked 💀",
                message_id,
            )

        return


    # ========================================================
    # FORGET
    # ========================================================

    forget_text = (
        extract_forget_text(
            prompt
        )
    )

    if forget_text:

        deleted = forget_memory(
            chat_id,
            user_id,
            forget_text,
        )

        if deleted:

            send_message(
                chat_id,
                "Forgotten 🫡",
                message_id,
            )

        else:

            send_message(
                chat_id,
                "I don't have that saved 🤷",
                message_id,
            )

        return


    # ========================================================
    # SHOW MEMORIES
    # ========================================================

    if is_memory_list_request(
        prompt
    ):

        memories = get_memories(
            chat_id,
            user_id,
            limit=20,
        )

        if not memories:

            send_message(
                chat_id,
                "I don't remember anything about you yet 😭",
                message_id,
            )

            return

        lines = []

        for i, item in enumerate(
            memories,
            start=1
        ):

            memory = item.get(
                "memory"
            )

            if memory:
                lines.append(
                    f"{i}. {memory}"
                )

        send_message(
            chat_id,
            "Here's what I remember 🧠\n\n"
            + "\n".join(lines),
            message_id,
        )

        return


    # ========================================================
    # FAST LOCAL RESPONSE
    # ========================================================

    local_reply = INSTANT_REPLIES.get(
        prompt.lower()
    )

    if local_reply:

        send_message(
            chat_id,
            local_reply,
            message_id,
        )

        return


    # ========================================================
    # RANDOM MAP
    # ========================================================

    if is_random_map_request(
        prompt
    ):

        chosen_map = (
            choose_random_map(
                prompt
            )
        )

        print(
            "Random map:",
            chosen_map
        )

        send_message(
            chat_id,
            chosen_map,
            message_id,
        )

        return


    # ========================================================
    # GET MEMORIES
    # ========================================================

    memories = get_memories(
        chat_id,
        user_id,
        limit=15,
    )


    # ========================================================
    # RECENT CHAT CONTEXT
    # ========================================================

    recent_chat_context = get_recent_chat_context(chat_id)

    # ========================================================
    # TYPING
    # ========================================================

    send_typing(
        chat_id
    )


    # ========================================================
    # GEMINI
    # ========================================================

    try:

        reply = ask_gemini(
            prompt,
            previous_message=(
                previous_bot_message
                if replying_to_buddy
                else None
            ),
            reply_context=reply_context,
            memories=memories,
            recent_chat_context=recent_chat_context,
        )

        if not reply:
            raise ValueError(
                "Empty Gemini response"
            )

        print(
            "Buddy:",
            reply
        )

        max_length = 4000

        if len(reply) <= max_length:

            send_message(
                chat_id,
                reply,
                message_id,
            )

        else:

            for i in range(
                0,
                len(reply),
                max_length,
            ):

                send_message(
                    chat_id,
                    reply[
                        i:i + max_length
                    ],
                    (
                        message_id
                        if i == 0
                        else None
                    ),
                )

    except Exception as e:

        print(
            "Gemini permanently failed:",
            repr(e)
        )

        send_message(
            chat_id,
            "Brain.exe crashed 💀",
            message_id,
        )


# ============================================================
# VERCEL HANDLER
# ============================================================

class handler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"Buddy is online."
        )


    def do_POST(self):

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(
                content_length
            )

            update = json.loads(
                body.decode("utf-8")
            )

            process_update(
                update
            )

            self.send_response(
                200
            )

            self.send_header(
                "Content-Type",
                "application/json",
            )

            self.end_headers()

            self.wfile.write(
                b'{"ok":true}'
            )

        except Exception as e:

            print(
                "Webhook error:",
                repr(e)
            )

            # Always acknowledge Telegram.
            self.send_response(
                200
            )

            self.send_header(
                "Content-Type",
                "application/json",
            )

            self.end_headers()

            self.wfile.write(
                b'{"ok":true}'
            )