#!/usr/bin/env python3
"""Interactive setup for Second Wind. Standard library only; needs gcloud + terraform.

  python3 scripts/setup.py config    # choose project, region, data sources -> terraform/terraform.tfvars,
                                     # create the Terraform state bucket
  python3 scripts/setup.py secrets   # fill secret values (hidden input) for everything in use
  python3 scripts/setup.py webhook   # point Telegram at the deployed bot

Secret values are piped straight to Secret Manager: never echoed, written to
disk, stored in tfvars or Terraform state.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TF_DIR = ROOT / "terraform"
TFVARS = TF_DIR / "terraform.tfvars"

SOURCES = {
    "garmin": "Garmin Connect: official Personal API (personal key)",
}

# Secrets the script creates itself (random), instead of asking for a value.
GENERATED = {"telegram_webhook_secret"}

# Where to get each secret value.
SECRET_HELP = {
    "telegram_webhook_secret": "Generated automatically: Telegram sends it to the bot with every request.",
    "telegram_bot_token": "Telegram → @BotFather → /newbot → the bot token.",
    "telegram_chat_id": "Send the bot any message, then open "
                        "https://api.telegram.org/bot<TOKEN>/getUpdates and take chat.id. "
                        "The bot answers only this chat.",
    "anthropic_api_key": "console.anthropic.com → API Keys.",
    "garmin_personal_api_key": "connect.garmin.com/app/settings/personalApiToken → Create Key. "
                               "The key is shown once; valid for 30/90/180 days or 1 year.",
}


def run(cmd: list[str], *, input_text: str | None = None, check: bool = True, capture: bool = True) -> str:
    res = subprocess.run(cmd, input=input_text, text=True, capture_output=capture)
    if check and res.returncode != 0:
        sys.exit(f"Command failed: {' '.join(cmd[:4])} …\n{res.stderr.strip()}")
    return (res.stdout or "").strip()


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


# ── config ────────────────────────────────────────────────────────────────────
def cmd_config(_args) -> None:
    project = ask("GCP project ID", run(["gcloud", "config", "get-value", "project"], check=False) or None)
    region = ask("Region", "europe-west3")

    print("\nData sources:")
    names = list(SOURCES)
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n:<7} {SOURCES[n]}")
    print("List the numbers, comma-separated, in priority order (the first wins on duplicates).")
    while True:
        picked = [p.strip() for p in ask("Sources", "1").split(",") if p.strip()]
        try:
            chosen = [names[int(p) - 1] for p in picked]
        except (ValueError, IndexError):
            print("Use numbers from the list.")
            continue
        if chosen and len(set(chosen)) == len(chosen):
            break
        print("Pick at least one source, without repeats.")

    schedule = ask("Collector schedule (cron, UTC)", "15 */4 * * *")

    if TFVARS.exists() and ask(f"{TFVARS.name} already exists. Overwrite? (y/N)", "N").lower() != "y":
        sys.exit("Left as is.")
    # The owner's id keys all data and the bot state: keep it if the install already has one.
    old = re.search(r'^owner_user_id\s*=\s*"([^"]+)"', TFVARS.read_text(encoding="utf-8"), re.M) if TFVARS.exists() else None
    owner_user_id = old.group(1) if old else str(uuid.uuid4())
    sources_hcl = ", ".join(f'"{s}"' for s in chosen)
    TFVARS.write_text(
        f'gcp_project_id = "{project}"\n'
        f'gcp_region     = "{region}"\n'
        f'data_sources   = [{sources_hcl}]\n'
        f'sync_schedule  = "{schedule}"\n'
        f'owner_user_id  = "{owner_user_id}" # never change: all data is stored under it\n'
        "# retired_secrets = [] # secrets no longer needed: kept and labelled status=retired\n",
        encoding="utf-8")
    print(f"\n✔ Wrote {TFVARS.relative_to(ROOT)} (git-ignored)")

    bucket = f"{project}-tfstate"
    exists = subprocess.run(["gcloud", "storage", "buckets", "describe", f"gs://{bucket}", "--project", project],
                            capture_output=True).returncode == 0
    if exists:
        print(f"✔ State bucket already exists: gs://{bucket}")
    elif ask(f"Create the Terraform state bucket gs://{bucket}? (Y/n)", "Y").lower() == "y":
        run(["gcloud", "storage", "buckets", "create", f"gs://{bucket}", "--project", project,
             "--location", region, "--uniform-bucket-level-access", "--public-access-prevention"])
        run(["gcloud", "storage", "buckets", "update", f"gs://{bucket}", "--versioning"])
        print(f"✔ Created gs://{bucket} (versioned)")

    print(f"""
Next:
  cd terraform
  terraform init -backend-config="bucket={bucket}"
  terraform apply -target=google_project_service.apis -target=google_artifact_registry_repository.repo -target=module.secrets
  python3 ../scripts/setup.py secrets
  # build and push the images (docs/setup.md, step 4), then:
  terraform apply
  python3 ../scripts/setup.py webhook""")


# ── secrets ───────────────────────────────────────────────────────────────────
def tf_output(name: str):
    return json.loads(run(["terraform", f"-chdir={TF_DIR}", "output", "-json", name]))


def project_id() -> str:
    m = re.search(r'gcp_project_id\s*=\s*"([^"]+)"', TFVARS.read_text(encoding="utf-8")) if TFVARS.exists() else None
    if not m:
        sys.exit("No terraform/terraform.tfvars yet; run first: python3 scripts/setup.py config")
    return m.group(1)


def has_value(secret: str, project: str) -> bool:
    out = run(["gcloud", "secrets", "versions", "list", secret, "--project", project,
               "--filter=state:enabled", "--limit=1", "--format=value(name)"], check=False)
    return bool(out)


def cmd_secrets(args) -> None:
    project = project_id()
    names = tf_output("secrets_to_fill")
    for name in names:
        if has_value(name, project) and name not in args.update:
            print(f"✔ {name}: already has a value (to replace it: --update {name})")
            continue
        print(f"\n{name}: {SECRET_HELP.get(name, '')}")
        if name in GENERATED:
            run(["gcloud", "secrets", "versions", "add", name, "--project", project, "--data-file=-"],
                input_text=secrets.token_urlsafe(32))
            print(f"  ✔ {name}: generated and stored (the value is not shown)")
            continue
        value = getpass.getpass("  value (hidden; empty = skip): ").strip()
        if not value:
            print("  skipped")
            continue
        run(["gcloud", "secrets", "versions", "add", name, "--project", project, "--data-file=-"], input_text=value)
        print(f"  ✔ {name}: new version added")


# ── webhook ───────────────────────────────────────────────────────────────────
def cmd_webhook(_args) -> None:
    project = project_id()
    bot_url = tf_output("bot_url")
    token = run(["gcloud", "secrets", "versions", "access", "latest", "--secret", "telegram_bot_token",
                 "--project", project])
    webhook_secret = run(["gcloud", "secrets", "versions", "access", "latest", "--secret", "telegram_webhook_secret",
                          "--project", project])
    url = f"https://api.telegram.org/bot{token}/setWebhook?" + urllib.parse.urlencode(
        {"url": f"{bot_url}/webhook", "secret_token": webhook_secret})
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed Telegram host
        body = json.loads(resp.read())
    print("✔ webhook set" if body.get("ok") else f"Telegram answered: {body.get('description')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config", help="project, region, data sources").set_defaults(fn=cmd_config)
    p = sub.add_parser("secrets", help="fill in secret values")
    p.add_argument("--update", nargs="*", default=[], help="replace these secrets (for example, after a new key)")
    p.set_defaults(fn=cmd_secrets)
    sub.add_parser("webhook", help="set the Telegram webhook").set_defaults(fn=cmd_webhook)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
