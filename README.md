# Buddy Bot

Buddy is a Discord gaming-friend bot for Smash Karts. It combines Gemini
conversation with deterministic, natural-language room creation.

## Features

- Casual English, Hindi, and Hinglish conversation
- Buddy mentions and replies
- `/ask` for normal Gemini chat
- `/room` for natural-language room creation
- Natural room requests such as:

  ```text
  bhai ek room bnado sf map me ctf khelna h 4 player ka
  dz me 2 players without team
  ek random room bana de
  6 bande ka room chahiye
  sf me 8 player ffa 20 minute ka
  ```

- Deterministic Python defaults and validation
- Smash Karts invitation URL generation without live server access

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set these variables in `.env`:

```env
DISCORD_TOKEN=your_discord_bot_token
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.1-flash-lite
CHAT_MAX_WORDS=25
BOT_PREFIX=/
CHAT_CHANNEL_IDS=
ROOM_REQUEST_CHANNEL_IDS=
GEMINI_TIMEOUT_SECONDS=20
GEMINI_MAX_CONCURRENCY=4
MESSAGE_COOLDOWN_SECONDS=3
```

Enable the **Message Content Intent** for the Discord application in the
Developer Portal. Invite the bot with permissions to read messages and send
messages. Mentions and replies always work. Set `CHAT_CHANNEL_IDS` to
comma-separated channel IDs where Buddy should answer normal messages without
a mention, and `ROOM_REQUEST_CHANNEL_IDS` for channels dedicated to room
requests. A channel can appear in both lists.

Gemini calls use the SDK's asynchronous client with an HTTP timeout, bounded
concurrency, and a per-user/channel cooldown to prevent duplicate bursts and
bot loops. A timed-out request is cancelled at the asyncio task boundary; the
SDK/network layer determines whether its underlying transport cancels.

## Run

```bash
python bot.py
```

## Commands

- Mention Buddy: `@Buddy bhai room bnade`
- Reply to a Buddy message or mention: `@Buddy explain this`
- Slash command: `/ask question`
- Slash command: `/room sf me 4 ka room bana`

Room defaults are applied in Python:

- Unspecified players: 6
- Unspecified mode: FFA for 4 or fewer players, otherwise CTF
- Unspecified map: random supported map
- Unspecified round: 10-minute timer
- FFA teams: enabled unless explicitly disabled
- CTF teams setting: ignored
- `machine_gun`, `invincibility`, and `lob_sentry`: disabled by default

The strict room parser and URL encoder remain available as reusable Python
modules in [api/room_creator.py](./api/room_creator.py) and
[api/room_intent.py](./api/room_intent.py).

## Tests

```bash
python -m pytest -q
```

The tests cover room intent defaults, validation, weapon masks, generated URLs,
and mocked Discord message/slash-command behavior.
