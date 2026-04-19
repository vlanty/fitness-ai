# Fitness AI

A self-hosted Telegram bot that collects your training data from **Strava** and **Garmin Connect**, stores it in **BigQuery**, and analyzes it using Claude AI — as a coach, sports doctor, and nutritionist.

**Cost: ~$1–2/month** (Claude API only; everything else fits in GCP free tier)

## What you get

- Daily automatic sync of activities, sleep, HRV, Body Battery, and daily stats
- Telegram bot with AI-powered analysis on demand:
  - `/coach` — training load, pace, progress toward your goal
  - `/health` — HRV trends, resting HR, sleep quality, fatigue flags
  - `/nutrition` — calorie targets, pre/post workout nutrition, hydration
  - `/today` — last workout + yesterday's stats
  - `/week` — current week summary

## Architecture

```
Cloud Scheduler (cron 01:00 UTC)
        │
        ▼
Cloud Run Job (collector)
  ├── Strava API
  └── Garmin Connect
        │
        ▼
BigQuery (free tier)
        │
        ▼
Cloud Run Service (Telegram bot)
        └── Claude API (on demand)
```

---

## Prerequisites

Make sure you have these installed on your machine:

- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- [Terraform](https://developer.hashicorp.com/terraform/install)
- [Docker](https://docs.docker.com/engine/install/)
- Python 3.x (for the Strava auth script)

---

## Step 1: Fork this repository

Click **Fork** on GitHub, then clone your fork:

```bash
git clone https://github.com/YOUR_USERNAME/fitness-ai.git
cd fitness-ai
```

---

## Step 2: Create a GCP project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown → **New Project**
3. Name it anything, e.g. `fitness-ai`
4. Note your **Project ID** (e.g. `fitness-ai-123456`)

Authenticate locally:

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

Enable billing on the project (required for Cloud Run and Scheduler — but you won't be charged; everything fits in free tier).

---

## Step 3: Get your credentials

### Strava

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an app (Website and Callback Domain: `localhost`)
3. Note your **Client ID** and **Client Secret**
4. Get your refresh token by running:

```bash
python3 -m venv ~/fitness-venv && source ~/fitness-venv/bin/activate
pip install requests
python scripts/strava_auth.py
```

The script opens a browser, you approve access, it prints your refresh token.

### Garmin Connect

Just your Garmin account **email** and **password**. The collector logs in automatically each night.

> **Note:** On first run Garmin may send a verification email. Log in manually at [connect.garmin.com](https://connect.garmin.com) to confirm, then retry.

### Telegram Bot

1. Open Telegram, find **@BotFather**
2. Send `/newbot`, follow prompts, get your **bot token**
3. Send any message to your new bot, then run:

```bash
curl "https://api.telegram.org/botYOUR_TOKEN/getUpdates"
```

Find `"chat":{"id":XXXXXXXX}` in the response — that's your **chat ID**.

### Anthropic API Key

Get it at [console.anthropic.com](https://console.anthropic.com) → **API Keys**.

---

## Step 4: Configure

```bash
cp terraform.tfvars.example terraform/terraform.tfvars
```

Edit `terraform/terraform.tfvars` and fill in all values:

```hcl
gcp_project_id       = "your-project-id"
gcp_region           = "europe-west3"
telegram_bot_token   = "..."
telegram_chat_id     = "..."
strava_client_id     = "..."
strava_client_secret = "..."
strava_refresh_token = "..."
garmin_email         = "..."
garmin_password      = "..."
anthropic_api_key    = "sk-ant-..."
sync_schedule        = "0 1 * * *"
```

> `terraform.tfvars` is in `.gitignore` — it will never be committed.

---

## Step 5: Deploy

### 5.1 Configure Docker for GCP

```bash
gcloud auth configure-docker YOUR_REGION-docker.pkg.dev
```

### 5.2 Bootstrap infrastructure (Artifact Registry first)

```bash
cd terraform
terraform init
terraform apply \
  -target=google_project_service.apis \
  -target=google_artifact_registry_repository.repo
```

### 5.3 Build and push Docker images

```bash
PROJECT=$(gcloud config get-value project)
REGION="europe-west3"
BASE="${REGION}-docker.pkg.dev/${PROJECT}/fitness-ai"

docker build -t ${BASE}/collector:latest ./collector
docker push ${BASE}/collector:latest

docker build -t ${BASE}/bot:latest ./bot
docker push ${BASE}/bot:latest
```

### 5.4 Deploy everything

```bash
terraform apply
```

Note the `bot_url` in the output.

### 5.5 Register Telegram webhook

```bash
BOT_URL=$(terraform output -raw bot_url)
curl "https://api.telegram.org/botYOUR_TOKEN/setWebhook?url=${BOT_URL}/webhook"
# Should return: {"ok":true}
```

---

## Step 6: Initial historical sync

On first run, load all your historical data:

```bash
gcloud run jobs execute fitness-collector \
  --region europe-west3 \
  --update-env-vars FULL_SYNC=true
```

This may take 5–30 minutes depending on how much data you have. After that, the cron job runs automatically every night at 01:00 UTC.

Monitor progress:

```bash
gcloud run jobs executions logs \
  $(gcloud run jobs executions list --job fitness-collector --region europe-west3 --format="value(name)" --limit=1) \
  --region europe-west3
```

---

## Step 7: Use the bot

Open Telegram and message your bot:

| Command | Description |
|---|---|
| `/start` | Show all commands |
| `/today` | Last workout + HRV + sleep + daily stats |
| `/week` | All workouts this week |
| `/coach` | AI analysis: training load, pace, recovery |
| `/health` | AI analysis: HRV trend, sleep, Body Battery |
| `/nutrition` | AI analysis: calorie targets, meal timing |

---

## Updating code

After making changes to collector or bot:

```bash
docker build -t ${BASE}/collector:latest ./collector && docker push ${BASE}/collector:latest
docker build -t ${BASE}/bot:latest ./bot && docker push ${BASE}/bot:latest

# Redeploy bot
gcloud run services update fitness-bot --region europe-west3 --image ${BASE}/bot:latest
```

---

## Troubleshooting

**Garmin login fails:**
Log in manually at [connect.garmin.com](https://connect.garmin.com) to confirm any verification email, then retry.

**Strava returns 401:**
Refresh token was revoked. Re-run `scripts/strava_auth.py` and update the secret:
```bash
echo -n "new_token" | gcloud secrets versions add strava_refresh_token --data-file=-
```

**Bot not responding:**
```bash
gcloud run services logs read fitness-bot --region europe-west3 --limit=50
```

**BigQuery tables missing:**
```bash
cd terraform && terraform apply
```