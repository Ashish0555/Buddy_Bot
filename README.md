# 🤖 Buddy — AI-Powered Telegram Group Bot

Buddy is a **Gen-Z style AI Telegram group bot** built for a Smash Karts community. It combines conversational AI, contextual replies, game utilities, automatic scoreboard analysis, and persistent game statistics in one serverless application.

**Tech:** Python • Google Gemini • Telegram Bot API • Supabase • Vercel

---

## ✨ Features

### 🧠 AI Group Chat
- Natural English, Hindi, and Hinglish conversations
- Understands slang, abbreviations, typos, memes, sarcasm, and casual speech
- Matches the user's language and tone
- Short, natural responses instead of formal assistant-style replies
- Understands Telegram reply/quote context
- Can participate in active group discussions without constantly spamming
- Uses relevant recent context without dragging unrelated old conversations into replies

### 💬 Telegram Interaction
- Responds to `@urrsbro_bot`
- Understands replies to Buddy
- Short follow-ups such as `ans`, `explain`, or `translate` can use the replied-to message as context
- Runs through Telegram webhooks

### 🏎️ Smash Karts
- Random map selection
- Full map names and common abbreviations
- FFA and CTF room creation
- Timer-based and score-target rooms
- Configurable player limits
- FFA team mode
- Weapon disabling
- Canonical Smash Karts room URL generation

### 📸 Automatic Scoreboard Analysis
Buddy can inspect images sent directly in the group and determine whether they contain a Smash Karts scoreboard.

For valid scoreboards it can extract:
- Game mode
- Map
- Player names
- Kills
- Deaths
- Flags
- Team colors
- Team scores
- Winners / draws

The target/crosshair column is ignored when calculating player statistics.

### 📊 Statistics & Leaderboards
- Persistent player statistics with Supabase
- Wins, losses, kills, deaths, flags
- Kill/death ratio
- Match history
- Daily, weekly, and monthly leaderboards
- Player-specific statistics
- Recent match lookup
- Admin controls for players, matches, and resets

> Scoreboard images do **not** need `@urrsbro_bot` in the caption. Buddy can process them automatically, while the full leaderboard is not posted automatically after every recorded match.

### ☁️ Serverless Deployment
- Telegram webhook architecture
- Vercel deployment
- Environment-variable based secrets
- No always-running local server required

---

## 🛠️ Tech Stack

| Technology | Purpose |
|---|---|
| **Python** | Core bot logic |
| **Google Gemini API** | Conversation and image analysis |
| **Telegram Bot API** | Group messages, photos, replies, and webhooks |
| **Supabase / PostgreSQL** | Players, matches, statistics, memories |
| **Vercel** | Serverless deployment |
| **python-dotenv** | Local environment variables |

---

## 🏗️ Architecture

```text
Telegram Group
      │
      ▼
Telegram Webhook
      │
      ▼
Vercel Serverless Function
      │
      ├── Text
      │    ├── Commands
      │    ├── Map / Room Logic
      │    └── Gemini
      │
      └── Image
           │
           ▼
       Gemini Vision
           │
           ├── Normal Image → Ignore
           │
           └── Smash Karts Scoreboard
                    │
                    ▼
                 Supabase
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Matches   Players   Memories
```

---

## 📁 Project Structure

```text
smash-karts-bot/
│
├── api/
│   ├── index.py          # Main bot, Telegram handling, AI, stats
│   └── room_creator.py   # Smash Karts room URL generation
│
├── requirements.txt
├── .gitignore
├── .env.local            # Local secrets; never commit
└── README.md
```

---

## 🚀 Local Setup

### 1. Clone

```bash
git clone https://github.com/<your-username>/<your-repository>.git
cd <your-repository>
```

### 2. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create `.env.local`:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_key
```

**Never commit `.env.local` or API keys.**

---

## 🗄️ Supabase

Buddy expects database tables/functions for:

```text
players
matches
match_players
memories
```

The match/stat system also uses database functions for atomic operations such as recording matches, deleting matches with stat adjustment, and resetting match/stat data.

Apply the SQL schema/functions from the project before deployment.

---

## 🤖 Telegram Setup

Create the bot using **@BotFather**.

For automatic processing of normal group messages and photos, configure Telegram group privacy appropriately so Buddy can receive the required updates.

Production webhook:

```text
https://<your-vercel-domain>/api/webhook
```

---

## 💬 Commands

### General

```text
@urrsbro_bot kya haal bc
@urrsbro_bot what are you doing
```

### Maps

```text
@urrsbro_bot random map
@urrsbro_bot choose a map
@urrsbro_bot pick a map from SI, DZ, LP
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
@urrsbro_bot leaderboard week
@urrsbro_bot leaderboard month
@urrsbro_bot stats <player>
@urrsbro_bot recent matches
@urrsbro_bot match <id>
```

### Memory

```text
@urrsbro_bot remember <text>
@urrsbro_bot forget <text>
@urrsbro_bot what do you remember about me
```

### Admin

```text
@urrsbro_bot show admin commands
```

---

## 🗺️ Map Shortcuts

| Shortcut | Map |
|---|---|
| `SF` | Smash Fort |
| `SI` | Smash Island |
| `GP` | Gravel Pit |
| `DZ` | Dropzone |
| `PP` | Pinball |
| `SD` | Showdown |
| `TE` | Temples |
| `T` / `TU` | Tunnels |
| `SS` | Space Station |
| `GY` | Graveyard |
| `LP` | Lava Pit |
| `SNS` | Slick 'n Slide |

### CTF Shortcuts

```text
SFC → Smash Fort CTF
SIC → Smash Island CTF
GYC → Graveyard CTF
LPC → Lava Pit CTF
TEC → Sky Arena Temples CTF
TUC → Sky Arena Tunnels CTF
```

---

## 📸 Automatic Image Processing

Users can simply send a screenshot to the group:

```text
                    Image
                      │
                      ▼
                Gemini Vision
                      │
              ┌───────┴───────┐
              ▼               ▼
        Normal image      Game scoreboard
              │               │
           Ignore        Extract stats
                              │
                              ▼
                         Save to DB
```

Only recognized Smash Karts scoreboards are stored as match data.

---

## 🏆 Winner Logic

### FFA

Player(s) with the highest kills are recorded as winners.

### CTF / Team

The application reads the two top-left team scores and applies the configured team-result logic.

Equal scores are recorded as a draw.

---

## 🔐 Security

Add this to `.gitignore`:

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

If a secret is accidentally exposed, rotate it immediately.

---

## ☁️ Deploy on Vercel

Install/update the Vercel CLI:

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

Configure these Vercel environment variables:

```text
TELEGRAM_TOKEN
GEMINI_API_KEY
SUPABASE_URL
SUPABASE_KEY
```

Then set the Telegram webhook to:

```text
https://<your-vercel-domain>/api/webhook
```

---

## 🧩 Design Philosophy

Buddy separates **generative AI** from **deterministic application logic**:

```text
Gemini
→ conversation understanding
→ language matching
→ image understanding

Python
→ commands
→ room generation
→ map handling
→ validation
→ winner/stat extraction rules

Supabase
→ persistent data
→ player statistics
→ match history
→ optional memories
```

This keeps game-critical operations predictable while allowing natural-language interaction.

---

## 🔮 Future Improvements

- Smarter group discussion participation
- Match summaries
- Player performance insights
- Per-group configuration
- Anti-spam controls
- Admin dashboard
- More Smash Karts maps/settings
- Usage and analytics dashboard

---

## 📄 License

Add the license that matches your intended use before publishing the repository.

---

## 👨‍💻 About

Buddy was built as a real-world AI + backend project combining conversational AI, computer vision, Telegram automation, and database-backed gaming analytics.
