# Setup

[← Back to README](../README.md)

Second Wind runs in your own **Google Cloud** project. Other clouds, AI models and an
on-premises version are planned (see [README](../README.md#where-it-runs)). Once it is installed,
the collector and the bot run on their own. For updates and fixes later, see [Operations](operations.md).

- [Prerequisites](#prerequisites)
- [Install](#install)
- [Data sources](#data-sources)
- [Bot settings](#bot-settings)

## Prerequisites

- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- [Terraform ≥ 1.7](https://developer.hashicorp.com/terraform/install)
- [Docker](https://docs.docker.com/engine/install/)
- Python 3.10+
- A Google Cloud project with billing enabled
- A Telegram bot token from @BotFather and an Anthropic API key
- Credentials for at least one [data source](#data-sources)

## Install

```bash
git clone https://github.com/YOUR_USERNAME/second-wind.git && cd second-wind
gcloud auth login && gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

1. **Configure:** project, region, data sources and schedule. This also creates the Terraform
   state bucket.
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
   REGION=europe-west3   # your region from `setup.py config`
   BASE="${REGION}-docker.pkg.dev/$(gcloud config get-value project)/fitness-ai"
   gcloud auth configure-docker ${REGION}-docker.pkg.dev
   docker build --platform linux/amd64 -f ../secondwind/apps/collector/Dockerfile -t ${BASE}/collector:latest .. && docker push ${BASE}/collector:latest
   docker build --platform linux/amd64 -f ../secondwind/apps/bot/Dockerfile -t ${BASE}/bot:latest .. && docker push ${BASE}/bot:latest
   terraform apply
   python3 ../scripts/setup.py webhook
   ```
5. **History** (optional, one-off):
   ```bash
   gcloud run jobs execute fitness-collector --region ${REGION} --update-env-vars BACKFILL_FROM=2024-01-01
   ```

Secret values never go into `terraform.tfvars` or Terraform state; Terraform only manages the
secret containers.

## Data sources

You pick sources at setup (`data_sources` in `terraform.tfvars`). **Order = priority**: when two
sources have the same workout or the same day, the one listed first wins
(details in [How it works](how-it-works.md#merging-several-sources)).

| Source | Data | Credentials |
|---|---|---|
| `garmin` | activities (+ original .FIT), sleep, HRV, stress, Body Battery, resting HR, steps, VO2 max | Personal API key |

### Garmin Connect

Uses the official [Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/)
([FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/)). It is for your
own data only, not for serving other users.

1. Create a key at [connect.garmin.com → Personal API Token](https://connect.garmin.com/app/settings/personalApiToken).
   It is shown once. Validity is 30 / 90 / 180 days or 1 year; Garmin emails about a week before
   it expires.
2. Enter it in `python3 scripts/setup.py secrets`.

To check what your own account returns, run `python3 scripts/garmin_probe.py` with your key.
When the key expires, see [Operations → Garmin key](operations.md#garmin-key).

### Why there is no Strava

Strava's API Policy (in force since 1 June 2026) forbids using Strava data with AI applications,
including putting it into a model's context, combining it with other data, and storing it for more
than 7 days. A coach that reads your data with an AI model cannot follow those rules, so Strava is
not a source. Record workouts with your watch; Garmin already provides them.

### Changing sources later

Edit `data_sources`, run `terraform apply`, then `setup.py secrets`. Secrets you no longer use go
to `retired_secrets`: they are kept and labelled `status=retired`, never deleted.

## Bot settings

Optional, in `terraform/terraform.tfvars`, then `terraform apply`:

| Variable | Default | What it does |
|---|---|---|
| `bot_language` | `""` | Language of menus, buttons and commands: `ru` or `en`. Empty = each user's Telegram app language (English if it's neither). Free-form answers always follow the language of the question. |
| `bot_models` | `{}` | Claude model per task, e.g. `{ chat = "claude-opus-5-5" }`. Tasks: `chat` (free-form questions), `analysis` (`/coach` `/health` `/nutrition`) — default `claude-sonnet-5-5`; `weekly`, `alerts`, `morning` (planned routine messages) — default `claude-haiku-5-5`. |

Model names are never shown to bot users.

**Adding a language:** copy `secondwind/core/locales/en.json` to `<code>.json`, translate the values and keep
the keys.
