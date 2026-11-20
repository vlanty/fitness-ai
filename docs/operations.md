# Operations

[← Back to README](../README.md)

- [Updating](#updating)
- [Rotating keys and tokens](#rotating-keys-and-tokens)
- [Troubleshooting](#troubleshooting)

The commands below assume `REGION` and `BASE` are set as in [Setup](setup.md#install), step 4.

## Updating

```bash
docker build --platform linux/amd64 -f secondwind/apps/collector/Dockerfile -t ${BASE}/collector:latest . && docker push ${BASE}/collector:latest
docker build --platform linux/amd64 -f secondwind/apps/bot/Dockerfile -t ${BASE}/bot:latest . && docker push ${BASE}/bot:latest
gcloud run jobs update fitness-collector --region ${REGION} --image ${BASE}/collector:latest
gcloud run services update fitness-bot --region ${REGION} --image ${BASE}/bot:latest
```

Both commands are needed: Cloud Run pins the image when the job or service is updated, so a
new push of `:latest` alone does not reach them.

The bot URL (and so the Telegram webhook) does not change between revisions.

**Roll back the bot:**

```bash
gcloud run revisions list --service fitness-bot --region ${REGION}
gcloud run services update-traffic fitness-bot --region ${REGION} --to-revisions PREVIOUS_REVISION=100
```

**Heart-rate zones for old activities.** Activities stored before the collector learned to read
HR zones from .FIT files get them with a one-off, re-runnable script that reads the archives
already in the raw bucket (add `--dry-run` to only look):

```bash
python3 scripts/backfill_fit_zones.py --project YOUR_PROJECT_ID
```

## Rotating keys and tokens

### Garmin key

When the key has expired, been lost or leaked:

1. On the [Garmin page](https://connect.garmin.com/app/settings/personalApiToken) delete the old
   key (this also invalidates its tokens) and create a new one.
2. `python3 scripts/setup.py secrets --update garmin_personal_api_key`
3. Nothing else: the next collector run picks up the `latest` version.

### Strava token

If Strava starts returning `400` on `/oauth/token`, the token was revoked: re-run
`python3 scripts/strava_auth.py`, then `python3 scripts/setup.py secrets --update strava_refresh_token`.

### Telegram bot token

Copy the whole new token, digits before the colon included.

1. @BotFather → `/revoke` → choose the bot → new token.
2. `python3 scripts/setup.py secrets --update telegram_bot_token`, then disable the old version:
   `gcloud secrets versions disable OLD_VERSION --secret telegram_bot_token`.
3. `gcloud run services update fitness-bot --region ${REGION} --revision-suffix token-$(date +%s)`:
   new instances read the new token.
4. `python3 scripts/setup.py webhook`: revoking the token removed the webhook.

If the bot fails to start with `Telegram rejected the bot token`, the stored value is wrong (often
a digit lost when pasting): store it again and repeat step 3.

## Troubleshooting

`/status` in the bot shows the last successful sync per source. For more:

```bash
gcloud run jobs executions list --job fitness-collector --region ${REGION} --limit 5
gcloud logging read 'resource.labels.job_name="fitness-collector" AND severity>=WARNING' --freshness=1d --limit 20
gcloud run services logs read fitness-bot --region ${REGION} --limit=50
```

**Garmin 401/403 on token.** The key expired or was deleted: see [Garmin key](#garmin-key).

**Bot silent, no `POST /webhook` in its logs.** Telegram is not delivering. Run
`python3 scripts/setup.py webhook`. This is needed after every Telegram token rotation, because
Telegram drops the webhook when a token is revoked.

**Bot silent, `rejected chat <id>` in its logs.** It answers only the chat in `telegram_chat_id`.
If that id is yours, store it and roll out a fresh revision (secrets are read at instance start):

```bash
gcloud run services logs read fitness-bot --region ${REGION} --limit=50 | grep "rejected chat"
python3 scripts/setup.py secrets --update telegram_chat_id
gcloud run services update fitness-bot --region ${REGION} --revision-suffix chatid-$(date +%s)
```

**Secrets in logs.** The bot never writes the Telegram token or the Anthropic key to the logs:
request URLs are not logged, and `secondwind/apps/bot/log_safety.py` masks anything token-shaped in every log
line and traceback.
