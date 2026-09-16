# Buddy Bot

Buddy is an AI-powered Telegram group bot built for a Smash Karts gaming community.

It combines conversational AI with game-specific automation, including Smash Karts room creation, map selection, scoreboard analysis, and player statistics.

## Features

### AI Chat

- Conversational replies in English, Hindi, and Hinglish
- Understands slang, abbreviations, typos, memes, sarcasm, and casual group-chat language
- Matches the language and tone of the current conversation
- Supports Telegram reply context
- Can participate in active group discussions without responding to every message
- Short responses designed for group conversations

### Smash Karts

- Random map selection
- Support for full map names and common abbreviations
- FFA and CTF room creation
- Timer and score-target modes
- Configurable player limits
- FFA team mode
- Weapon disabling
- Smash Karts room URL generation

### Automatic Scoreboard Processing

Buddy can automatically inspect images sent in the group.

When an image is a Smash Karts scoreboard, the bot can extract:

- Match mode
- Map
- Player names
- Kills
- Deaths
- Flags
- Team colors
- Team scores
- Match result

Normal images are ignored.

Scoreboard screenshots do not need to mention the bot in their caption.

### Player Statistics

Match data is stored in Supabase and can be used for:

- Wins and losses
- Kills and deaths
- Kill/death ratio
- Flags
- Match history
- Daily, weekly, and monthly leaderboards
- Individual player statistics
- Match lookup

The bot does not automatically post the leaderboard after every recorded match.

## Tech Stack

- Python
- Google Gemini API
- Telegram Bot API
- Supabase / PostgreSQL
- Vercel

## Project Structure

```text
Buddy_Bot/
├── api/
│   ├── index.py
│   └── room_creator.py
├── requirements.txt
├── .gitignore
└── README.md
```

## How It Works

```text
Telegram Group
      |
      v
Telegram Webhook
      |
      v
Vercel
      |
      +--------------------+
      |                    |
      v                    v
   Text                 Images
      |                    |
      v                    v
Commands / Gemini    Gemini Vision
      |                    |
      |              Smash Karts?
      |               /       \
      |             No         Yes
      |             |           |
      |           Ignore        v
      |                    Extract stats
      |                         |
      +-------------------------+
                                |
                                v
                             Supabase
```

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/Ashish0555/Buddy_Bot.git
cd Buddy_Bot
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Add environment variables

Create `.env.local`:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_key
```

Never commit this file.

## Telegram Setup

Create the bot using BotFather and add it to your Telegram group.

For Buddy to process normal group messages and automatically inspect images, configure the bot's Telegram group privacy settings so the required updates are available to the bot.

The webhook endpoint used in production is:

```text
https://<your-vercel-domain>/api/webhook
```

## Example Commands

### Chat

```text
@urrsbro_bot kya haal bc
@urrsbro_bot what are you doing
```

### Maps

```text
@urrsbro_bot random map
@urrsbro_bot choose a map
```

### Room Creation

```text
@urrsbro_bot create room ffa timer 10 DZ players 12
@urrsbro_bot create room ctf timer 10 LP players 8
@urrsbro_bot create room ffa score 10 SI players 12 teams
```

### Statistics

```text
@urrsbro_bot leaderboard
@urrsbro_bot leaderboard today
@urrsbro_bot stats <player>
@urrsbro_bot recent matches
@urrsbro_bot match <id>
```

## Map Shortcuts

| Shortcut | Map |
|---|---|
| SF | Smash Fort |
| SI | Smash Island |
| GP | Gravel Pit |
| DZ | Dropzone |
| PP | Pinball |
| SD | Showdown |
| TE | Temples |
| T / TU | Tunnels |
| SS | Space Station |
| GY | Graveyard |
| LP | Lava Pit |
| SNS | Slick 'n Slide |

CTF shortcuts:

```text
SFC -> Smash Fort CTF
SIC -> Smash Island CTF
GYC -> Graveyard CTF
LPC -> Lava Pit CTF
TEC -> Sky Arena Temples CTF
TUC -> Sky Arena Tunnels CTF
```

## Database

Buddy uses Supabase for persistent data.

The application works with:

```text
players
matches
match_players
memories
```

The match system also uses database functions for operations such as recording matches, deleting matches with stat adjustments, and resetting match data.

Apply the database schema and required functions before deployment.

## Deployment

Install the Vercel CLI:

```bash
npm install -g vercel@latest
```

Login:

```bash
vercel login
```

Deploy:

```bash
vercel --prod
```

Set these environment variables in Vercel:

```text
TELEGRAM_TOKEN
GEMINI_API_KEY
SUPABASE_URL
SUPABASE_KEY
```

Then configure the Telegram webhook:

```text
https://<your-vercel-domain>/api/webhook
```

## Security

Keep secrets out of Git:

```gitignore
venv/
__pycache__/
*.pyc
.vercel/
.env*
```

Never commit:

```text
TELEGRAM_TOKEN
GEMINI_API_KEY
SUPABASE_KEY
VERCEL_OIDC_TOKEN
```

If a credential is exposed, rotate it immediately.

## Design

Buddy keeps AI-driven behavior separate from deterministic application logic.

```text
Gemini
- Conversation understanding
- Language matching
- Image understanding

Python
- Telegram handling
- Commands
- Map selection
- Room generation
- Validation
- Match result logic

Supabase
- Player data
- Match history
- Statistics
- Optional memories
```

This makes the bot flexible for conversation while keeping game-related operations predictable.

## Future Work

- Better group discussion participation
- Match summaries
- Player performance analysis
- Per-group configuration
- Anti-spam controls
- More Smash Karts maps and settings
- Admin dashboard

## Author

Ashish Meena

GitHub: https://github.com/Ashish0555
