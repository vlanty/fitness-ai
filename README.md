# Fitness AI

A self-hosted Telegram bot that collects your training and health data from the
sources you choose (**Garmin Connect**, **Strava**, more can be added), stores it
in **BigQuery**, and analyzes it with Claude as a coach, sports doctor and
nutritionist.

**Cost: ~$1–2/month** (Claude API; GCP usage fits the free tier).

## What you get

- Sync every few hours of activities and daily health (sleep, HRV, resting HR, stress, Body Battery, steps, VO2 max — depending on the source)
- Telegram bot (answers only your chat):
  - just ask — «как прошла последняя тренировка?», «как я спал вчера?»
  - `/today` — last workout + latest day of health data
  - `/week` — current week summary
  - `/coach`, `/health`, `/nutrition` — AI analysis
  - `/status` — freshness of each data source

## Architecture

```
Cloud Scheduler (every 4 h)
        │
        ▼
Cloud Run Job "collector" ── sources/garmin.py ── Garmin Personal API
        │                └─ sources/strava.py ── Strava API
        ├── raw files (.FIT) ──► GCS bucket <project>-fitness-raw
        ▼
BigQuery: activities, health_daily, ingest_state
          views: activities_unified, health_daily_unified
        │
        ▼
Cloud Run Service "bot" (Telegram webhook) ── Claude API
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
- `activities_unified` removes duplicates: the same workout from two sources = start time within **±5 min** and duration within **10 %**. The row from the source listed first in `data_sources` is kept; `also_in` shows where else it was found. Sources no longer enabled (e.g. old Strava history) rank last but their unique workouts stay visible.
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
2. **Base infrastructure** (fresh install: delete `terraform/imports.tf` first — it is only for adopting an existing deployment):
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
- **Bot silent** — it answers only `telegram_chat_id`; check the value, rejected chats are logged.
- `/status` in the bot shows the last successful sync per source.
