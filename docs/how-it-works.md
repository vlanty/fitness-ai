# How it works

[← Back to README](../README.md)

- [Architecture](#architecture)
- [Collecting data](#collecting-data)
- [Merging several sources](#merging-several-sources)
- [What Garmin provides](#what-garmin-provides)
- [Training load](#training-load)
- [Readiness and training status](#readiness-and-training-status)
- [Heart-rate zones](#heart-rate-zones)
- [Adding a data source](#adding-a-data-source)

## Architecture

```
Cloud Scheduler (every 4 h)
        │
        ▼
Cloud Run Job "collector"  ── DATA_SOURCES=garmin (order = priority)
        │   sources/garmin.py ── Garmin Personal API (personal key → 24 h token)
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
        ├── secondwind/core/estimates.py ── readiness & training status (our estimates)
        ├── secondwind/core/locales/     ── texts in ru / en
        ├── secondwind/core/models.py    ── which model tier does which task
        └── Claude API      (Sonnet 5.5 for questions and analysis, Haiku 5.5 for routine messages)

Secrets: Secret Manager (values never in Terraform)   State: GCS bucket <project>-tfstate
```

Cloud resources keep the short technical prefix `fitness-` (`fitness-bot`, `fitness-collector`,
dataset `fitness`), so existing installs update in place without recreating anything.

## Collecting data

- The collector runs every 4 hours and pulls activities and daily health: sleep, HRV, resting
  HR, stress, Body Battery, steps and VO2 max, depending on the source.
- On first install it loads your full history.
- Each source is pulled separately with its own cursor (`ingest_state`). One failing does not
  stop the others, but the job run is marked failed and the bot shows a warning.
- Writes are `MERGE` on `source + id/date`, so re-running is always safe.

**Garmin specifics.** The collector exchanges your personal key for a 24-hour bearer token on
every run (there are no refresh tokens). Garmin does not publish rate limits and sends no
rate-limit headers; no 429 was seen while testing. The collector still paces requests (≥ 0.5 s
apart), retries 429/5xx with exponential backoff (honouring `Retry-After`), downloads at most
`FIT_DOWNLOAD_LIMIT` (50) .FIT files per run and reads health in 28-day windows: one window is an
activity list + 4 health requests (+ .FIT downloads). The API returns old data: activities and
daily health from at least 25 months back were available when tested.

## Merging several sources

- Tables keep everything that arrived, with a `source` column.
- **`activities_unified`** removes duplicates. The same workout from two sources = start time
  within **±5 min** and moving *or* elapsed duration within **10 %**. The row from the source
  listed first in `data_sources` is kept; `also_in` shows where else it was found. Sources no
  longer enabled (e.g. a watch you no longer use) rank last, but their unique workouts stay visible.
- **`health_daily_unified`** takes each field per day from the highest-priority source that
  has it.

## What Garmin provides

Checked with `scripts/garmin_probe.py`:

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

Because readiness and status are not available, Second Wind calculates its own (below).

## Training load

Garmin's readiness and status models are closed, but their inputs are documented, and the heavy
part, per-activity EPOC load and training effect, comes from Garmin itself. The view
`training_load_daily` builds on it:

| Metric | How it is calculated |
|---|---|
| Acute load | sum of `training_load` over the last 7 days |
| Chronic load | weekly average over the last 28 days |
| Load ratio | acute ÷ chronic, with Garmin's bands: < 0.8 low, 0.8–1.4 optimal, above that high |
| Load focus | 28-day split into low aerobic / high aerobic / anaerobic, from `trainingEffectLabel` |
| VO2 max trend | latest VO2 max and its change over 28 days |
| HRV normal range | `hrv_baseline_low/high`, from Garmin's HRV baseline |

## Readiness and training status

The bot shows **readiness** (0–100) and **training status** as *Second Wind's own estimates, not
Garmin values*. They are transparent rules in `secondwind/core/estimates.py` over the same inputs Garmin lists:

- **Readiness:** sleep score, 3-night sleep, HRV against its baseline, load ratio and 3-day
  stress. Recovery time is not used, because it is not available.
- **Training status:** VO2 max trend, load ratio and HRV status.

Recovery time and performance condition are not estimated.

## Heart-rate zones

The zones are the ones your watch uses. The collector reads them from each new .FIT file
(`time_in_zone`: zone bounds, the method — % of lactate threshold, max HR or HR reserve — and the
threshold HR), so the bot's zones match the watch and Garmin's time in zone. Garmin keeps zones
per sport; the bot prefers the running ones.

Without zones from the watch (e.g. a source without .FIT files), the bot falls back in this order:

1. threshold HR from `/profile` (Friel zones);
2. Karvonen, with resting HR from Garmin's 7-day average;
3. % of max HR.

`/profile` also shows how the threshold on the watch changed over about 3 months.

## Adding a data source

1. `secondwind/apps/collector/sources/<name>.py`: subclass `Source`, set `name` and `required_env`, implement
   `fetch(start, end, known_ids) -> Batch` returning rows for `activities` / `health_daily`
   (see `sources/base.py`).
2. Register it in `secondwind/apps/collector/sources/__init__.py`.
3. Terraform: add its secrets to `local.source_secrets` (`terraform/main.tf`) and the name to the
   `data_sources` validation.
4. `scripts/setup.py`: add it to `SOURCES` and its secrets to `SECRET_HELP`.
5. Document it in [Setup → Data sources](setup.md#data-sources).
