<p align="center">
  <img src="docs/avatar.png" alt="Vita, the Second Wind bot" width="200">
</p>

<h1 align="center">Second Wind</h1>

<p align="center">
  <b>A personal AI coach that lives in your Telegram.</b><br>
  🔒 your own Google Cloud project · 💸 about $1–2 a month · 🌍 Russian and English
</p>

Meet **Vita**. She reads your Garmin and Strava data and answers like a coach, a sports doctor
and a nutritionist who know your workouts, your sleep and your recovery. Ask in plain words and
get an answer based on your own numbers, not generic advice.

## Highlights

💬 **Ask anything.** *"Am I recovered enough for intervals today?"* Vita looks at your recent
workouts, sleep, HRV, resting heart rate and stress. If data is missing or stale, she says so
instead of guessing.

🧠 **Expert reviews in one tap.** `/coach` for training, `/health` for recovery and warning
signs, `/nutrition` for calories, fuelling and hydration.

📊 **Your numbers instantly, without AI.** `/today` with readiness and training status, `/week`,
`/month`, `/compare` against the previous period, `/records` for best efforts from 1 km to the
marathon, and `/charts` for volume, load, HRV, resting HR and running efficiency.

🎯 **Coaching that fits you.** Set a race goal with `/goal`, and your age, weight, threshold and
injuries with `/profile`. Heart-rate zones come straight from your watch.

🔄 **Data collects itself.** Garmin and Strava sync every 4 hours, are merged without duplicates,
and your full history is loaded on first install.

⚙️ **You stay in control.** Pick the AI mode and see what it costs this month in `/settings`.
Only your chat gets answers. Your data is stored only in your own cloud project; for AI answers,
a summary of it is sent to the AI model.

## Documentation

| | |
|---|---|
| 🚀 [Setup](docs/setup.md) | install in your Google Cloud project, connect Garmin and Strava, bot settings |
| 🛠 [Operations](docs/operations.md) | updating, rotating keys and tokens, troubleshooting |
| 🔬 [How it works](docs/how-it-works.md) | architecture, merging sources, how load, readiness, status and zones are calculated |
| 🤖 [Commands](docs/commands.md) | every bot command and what it shows |

## Support

Second Wind is free for non-commercial use. If Vita helps your training, you can support the project:

- 💜 [GitHub Sponsors](https://github.com/sponsors/vlanty)
- ☕ [Ko-fi](https://ko-fi.com/vlant)

## License

Second Wind is **source-available** under the [PolyForm Noncommercial License 1.0.0](LICENSE.md).

- ✅ Free to install and run for yourself, your friends, your club or a non-profit, for any number
  of users, as long as nobody pays for it.
- ❌ Charging users (subscriptions, paid access, ads or any other commercial use) needs a separate
  commercial license. To ask for one, [open an issue](https://github.com/vlanty/second-wind/issues).

This is a plain-language summary; the [license text](LICENSE.md) is what applies. Garmin, Strava
and AI-provider terms apply on top of it.
