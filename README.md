<p align="center">
  <img src="docs/avatar.png" alt="Vita, the Second Wind bot" width="200">
</p>

# Second Wind

*Второе дыхание.* A self-hosted Telegram bot, **Vita** (Вита), that collects your training and health data from the
sources you choose (**Garmin Connect**, **Strava**, more can be added), stores it
in **BigQuery**, and analyzes it with Claude as a coach, sports doctor and
nutritionist.

**Cost: ~$1–2/month** for typical use. GCP usage fits the free tier; you pay for Claude
API calls only — one question to the bot is about 4k input + 0.7k output tokens on
Claude Sonnet 5.5, roughly $0.015.

## What you get

- Sync every few hours of activities and daily health (sleep, HRV, resting HR, stress, Body Battery, steps, VO2 max — depending on the source)
- Telegram bot (answers only your chat) in **Russian or English**:
  - just ask in any language — "how did my last workout go?", «как я спал вчера?» — the answer comes in the language of the question
  - `/menu` (or `/start`) — buttons for everything below; the same commands are in Telegram's **Menu** list
  - the bot's name follows the Telegram app language: **Вита · Второе дыхание** in Russian, **Vita · Second Wind** otherwise (set by the bot itself on start, no BotFather step)
  - `/today` — last workout, latest health day, training load, readiness and training status (our own estimates)
  - `/week` — current week summary
  - `/coach`, `/health`, `/nutrition` — AI analysis
  - `/status` — freshness of each data source
- AI answers are formatted for Telegram: bold section headings, bullet lists, long answers split into several messages
- Training load from Garmin's per-activity EPOC: acute / chronic load, load ratio, load focus, VO2 max trend
- Full history on first install (`BACKFILL_FROM`), several sources merged without duplicates

## Architecture

```
Cloud Scheduler (every 4 h)
        │
        ▼
Cloud Run Job "collector"  ── DATA_SOURCES=garmin,strava (order = priority)
        │   sources/garmin.py ── Garmin Personal API (personal key → 24 h token)
        │   sources/strava.py ── Strava API (OAuth refresh token)
        ├── raw files (.FIT) ──► GCS bucket <project>-fitness-raw
        ▼
BigQuery dataset "fitness"
  tables: activities, health_daily      (one row per source + id/day, MERGE = idempotent)
          ingest_state                  (cursor and last status per source)
  views:  activities_unified            (duplicates across sources removed)
          health_daily_unified          (each field from the highest-priority source)
          training_load_daily           (acute/chronic load, ratio, focus, VO2 max trend)
        │
        ▼
Cloud Run Service "bot" (Telegram webhook, owner chat only)
        ├── bot/estimates.py ── readiness & training status (our estimates)
        ├── bot/locales/    ── texts in ru / en
        ├── bot/models.py   ── which Claude model does which task
        └── Claude API      (Sonnet 5.5 for questions and analysis, Haiku 5.5 for routine messages)

Secrets: Secret Manager (values never in Terraform)   State: GCS bucket <project>-tfstate
```

## Data sources

You pick sources at setup (`data_sources` in `terraform.tfvars`). **Order = priority.**

| Source | Data | Credentials |
|---|---|---|
| `garmin` | activities (+ original .FIT), sleep, HRV, stress, Body Battery, resting HR, steps, VO2 max | Personal API key |
| `strava` | activities only | OAuth app + refresh token |

### Several sources at once

- Each source is pulled separately with its own cursor; one failing does not stop the others, but the job run is marked failed and the bot shows a warning.
- Tables keep everything that arrived (`source` column, key `source + id/date`).
- `activities_unified` removes duplicates: the same workout from two sources = start time within **±5 min** and moving *or* elapsed duration within **10 %**. The row from the source listed first in `data_sources` is kept; `also_in` shows where else it was found. Sources no longer enabled (e.g. old Strava history) rank last but their unique workouts stay visible.
- `health_daily_unified` takes each field per day from the highest-priority source that has it.

### Garmin Connect (official Personal API)

- Docs: [Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/), [FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/).
- Create a key at [connect.garmin.com → Personal API Token](https://connect.garmin.com/app/settings/personalApiToken). It is shown once; validity 30 / 90 / 180 days or 1 year; Garmin emails ~a week before expiry.
- The collector exchanges the key for a 24-hour bearer token on every run (no refresh tokens exist).
- **Limits:** Garmin does not publish rate limits and sends no rate-limit headers; no 429 was seen while testing. The collector paces requests (≥0.5 s apart), retries 429/5xx with exponential backoff (honours `Retry-After`), downloads at most `FIT_DOWNLOAD_LIMIT` (50) .FIT files per run and reads health in 28-day ranges: one window = activity list + 4 health requests (+ .FIT downloads).
- **History:** the API returns old data — activities and daily health from at least 25 months back were available when tested (as far back as the account has data).
- **What you get** (checked with `scripts/garmin_probe.py`):

  | Data | Available |
  |---|---|
  | Sleep: duration, phases, score, start/end | ✅ |
  | HRV: last night, 7-day avg, status | ✅ |
  | Resting HR, stress, Body Battery (min/max/charged/drained), steps, active calories, respiration | ✅ (daily summary) |
  | VO2 max | ✅ on days Garmin recalculates it (health API; not in .FIT) |
  | Activity: HR, pace, aerobic/anaerobic training effect, training load | ✅ |
  | Original .FIT file | ✅ (ZIP) |
  | Daily average SpO2 | ❌ |
  | Training readiness, training status | ❌ (404) |
  | Recovery time, performance condition | ❌ only inside undocumented .FIT message 140, not decoded |

  Run `python3 scripts/garmin_probe.py` with your key to check your own account.

### Training load and estimates (computed by Second Wind)

Garmin's readiness / status models are closed, but their inputs are documented and the
heavy part — per-activity EPOC load and training effect — comes from Garmin itself.

- View `training_load_daily`: acute load (7-day sum of `training_load`), chronic load
  (weekly average over 28 days), load ratio with Garmin's bands (< 0.8 low, 0.8–1.4 optimal,
  above high), 28-day load focus (low aerobic / high aerobic / anaerobic from
  `trainingEffectLabel`), VO2 max and its 28-day change.
- HRV personal range (`hrv_baseline_low/high`) from Garmin's HRV baseline.
- Bot shows **readiness** (0–100) and **training status** as *our own estimates, not Garmin
  values* — transparent rules in `bot/estimates.py` over the same inputs Garmin lists
  (readiness: sleep score, 3-night sleep, HRV vs baseline, load ratio, 3-day stress — without
  recovery time; status: VO2 max trend, load ratio, HRV status).
- Recovery time and performance condition are not estimated.
- Personal use only: the Personal API is for your own data, not for serving other users.

**Rotate / re-issue the key** (expired, lost or leaked):

1. On the Garmin page delete the old key (this also invalidates its tokens) and create a new one.
2. `python3 scripts/setup.py secrets --update garmin_personal_api_key`
3. Nothing else: the next collector run picks up the `latest` version.

### Strava

1. [strava.com/settings/api](https://www.strava.com/settings/api): create an app (callback domain `localhost`).
2. `python3 scripts/strava_auth.py` prints a refresh token.
3. Enter Client ID, Client Secret and the refresh token in `setup.py secrets`.

If Strava starts returning `400` on `/oauth/token`, the token was revoked: re-run `strava_auth.py` and `setup.py secrets --update strava_refresh_token`.

## Bot settings

Optional, in `terraform/terraform.tfvars`, then `terraform apply`:

| Variable | Default | What it does |
|---|---|---|
| `bot_language` | `""` | Language of menus, buttons and commands: `ru` or `en`. Empty = each user's Telegram app language (English if it's neither). Free-form answers always follow the language of the question. |
| `bot_models` | `{}` | Claude model per task, e.g. `{ chat = "claude-opus-5-5" }`. Tasks: `chat` (free-form questions), `analysis` (`/coach` `/health` `/nutrition`) — default `claude-sonnet-5-5`; `weekly`, `alerts`, `morning` (planned routine messages) — default `claude-haiku-5-5`. |

Model names are never shown to bot users. To add a language, copy `bot/locales/en.json`
to `<code>.json`, translate the values and keep the keys.

**Secrets in logs.** The bot never writes the Telegram token or the Anthropic key to the
logs: request URLs are not logged, and `bot/log_safety.py` masks anything token-shaped in
every log line and traceback.

## Setup

Prerequisites: [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), [Terraform ≥ 1.7](https://developer.hashicorp.com/terraform/install), [Docker](https://docs.docker.com/engine/install/), Python 3.10+.

```bash
git clone https://github.com/YOUR_USERNAME/fitness-ai.git && cd fitness-ai
gcloud auth login && gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID     # billing must be enabled
```

1. **Configure** — project, region, data sources, schedule; creates the state bucket:
   ```bash
   python3 scripts/setup.py config
   ```
2. **Base infrastructure:**
   ```bash
   cd terraform
   terraform init -backend-config="bucket=YOUR_PROJECT_ID-tfstate"
   terraform apply -target=google_project_service.apis -target=google_artifact_registry_repository.repo -target=module.secrets
   ```
3. **Secret values** (hidden input, sent straight to Secret Manager):
   ```bash
   python3 ../scripts/setup.py secrets
   ```
4. **Images and the rest:**
   ```bash
   REGION=europe-west3; BASE="${REGION}-docker.pkg.dev/$(gcloud config get-value project)/fitness-ai"
   gcloud auth configure-docker ${REGION}-docker.pkg.dev
   docker build --platform linux/amd64 -t ${BASE}/collector:latest ../collector && docker push ${BASE}/collector:latest
   docker build --platform linux/amd64 -t ${BASE}/bot:latest ../bot && docker push ${BASE}/bot:latest
   terraform apply
   python3 ../scripts/setup.py webhook
   ```
5. **History** (optional, one-off):
   ```bash
   gcloud run jobs execute fitness-collector --region europe-west3 --update-env-vars BACKFILL_FROM=2024-01-01
   ```

Secret values never go into `terraform.tfvars` or Terraform state; Terraform only manages the secret containers.

## Updating

```bash
docker build --platform linux/amd64 -t ${BASE}/collector:latest ./collector && docker push ${BASE}/collector:latest
docker build --platform linux/amd64 -t ${BASE}/bot:latest ./bot && docker push ${BASE}/bot:latest
gcloud run services update fitness-bot --region europe-west3 --image ${BASE}/bot:latest
```

The bot URL (and so the Telegram webhook) does not change between revisions. Roll back with
`gcloud run services update-traffic fitness-bot --region europe-west3 --to-revisions PREVIOUS_REVISION=100`.

Changing sources = edit `data_sources`, `terraform apply`, `setup.py secrets`. Secrets you no longer
use go to `retired_secrets`: they are kept and labelled `status=retired`, never deleted.

## Adding a data source

1. `collector/sources/<name>.py`: subclass `Source`, set `name` and `required_env`, implement `fetch(start, end, known_ids) -> Batch` returning rows for `activities` / `health_daily` (see `sources/base.py`).
2. Register it in `collector/sources/__init__.py`.
3. Terraform: add its secrets to `local.source_secrets` (`terraform/main.tf`) and the name to the `data_sources` validation.
4. `scripts/setup.py`: add it to `SOURCES` and its secrets to `SECRET_HELP`; document it above.

## Troubleshooting

```bash
gcloud run jobs executions list --job fitness-collector --region europe-west3 --limit 5
gcloud logging read 'resource.labels.job_name="fitness-collector" AND severity>=WARNING' --freshness=1d --limit 20
gcloud run services logs read fitness-bot --region europe-west3 --limit=50
```

- **Garmin 401/403 on token** — the key expired or was deleted: see “Rotate the key”.
- **Bot silent, no `POST /webhook` in its logs** — Telegram is not delivering. Run `python3 scripts/setup.py webhook`; this is needed after every token rotation (below), because Telegram drops the webhook when a token is revoked.
- **Bot silent, `rejected chat <id>` in its logs** — it answers only the chat in `telegram_chat_id`. If that id is yours, store it and roll out a fresh revision (secrets are read at instance start):
  ```bash
  gcloud run services logs read fitness-bot --region europe-west3 --limit=50 | grep "rejected chat"
  python3 scripts/setup.py secrets --update telegram_chat_id
  gcloud run services update fitness-bot --region europe-west3 --revision-suffix chatid-$(date +%s)
  ```
- **Rotate the Telegram bot token** (leaked or lost) — copy the whole new token, digits before the colon included:
  1. @BotFather → `/revoke` → choose the bot → new token.
  2. `python3 scripts/setup.py secrets --update telegram_bot_token`, then disable the old version: `gcloud secrets versions disable OLD_VERSION --secret telegram_bot_token`.
  3. `gcloud run services update fitness-bot --region europe-west3 --revision-suffix token-$(date +%s)` — new instances read the new token.
  4. `python3 scripts/setup.py webhook` — revoking the token removed the webhook.

  If the bot fails to start with `Telegram rejected the bot token`, the stored value is wrong (often a digit lost when pasting): store it again and repeat step 3.
- **Roll back the bot** — `gcloud run revisions list --service fitness-bot --region europe-west3`, then `gcloud run services update-traffic fitness-bot --region europe-west3 --to-revisions REVISION=100`.
- `/status` in the bot shows the last successful sync per source.
