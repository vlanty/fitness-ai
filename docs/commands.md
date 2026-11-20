# Bot commands

[← Back to README](../README.md)

All commands are also in Telegram's **Menu** list and as buttons under `/menu` (or `/start`).
Anything else you type is a free-form question: Vita answers in the language you asked in.

## Expert reviews (AI)

| Command | What you get |
|---|---|
| 🏃 `/coach` | what went well, what didn't, what to change in your training |
| ❤️ `/health` | recovery, fatigue and warning signs from HRV, resting HR, sleep and Body Battery |
| 🥗 `/nutrition` | calories for training and rest days, what to eat before and after, hydration |

## Numbers (no AI, instant)

| Command | What you get |
|---|---|
| 📍 `/today` | last workout, last night, training load, **readiness** and **training status** |
| 📅 `/week` | everything you did this week |
| 🗓 `/month` | this month so far next to the whole previous month: workouts by sport, time, running km, pace and heart rate, elevation, load, time in heart-rate zones |
| ⚖️ `/compare` | this week, month, 4 weeks or year against the same stretch of time before it |
| 🏅 `/records` | best 1 km, mile, 5 km, 10 km, half and marathon efforts, all-time and over 12 months |
| 📈 `/charts` | weekly running volume, training load, overnight HRV against your normal range, resting heart rate, running efficiency (metres per heartbeat) |
| 🔄 `/status` | when each data source last synced, with a warning if one is stale |

Readiness and training status are Second Wind's own estimates, see
[How it works](how-it-works.md#readiness-and-training-status).

## About you

| Command | What you get |
|---|---|
| 🎯 `/goal` | a race, its date and your target time; without a goal she coaches for steady progress |
| 👤 `/profile` | age, weight, heart rate, threshold and injuries, so the advice fits you; heart-rate zones come from your watch |

## Settings

`/settings`: menu language, AI mode (💰 Economy, ⚖️ Normal or 🧠 Maximum, each with its price
shown) and how much the AI cost this month.

## Cost

Google Cloud usage fits the free tier, so you only pay for Claude API calls. One question is
about 4k input + 0.7k output tokens on Claude Sonnet 5.5, roughly $0.015.

## Bot name

The bot sets its own name to match the Telegram app language: "Vita · Second Wind" by default and
"Вита · Второе дыхание" for Russian. You don't need to change anything in BotFather.
