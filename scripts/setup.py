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
    "garmin": "Garmin Connect — официальный Personal API (личный ключ)",
}

# Secrets the script creates itself (random), instead of asking for a value.
GENERATED = {"telegram_webhook_secret"}

# Where to get each secret value.
SECRET_HELP = {
    "telegram_webhook_secret": "Создаётся автоматически: Telegram присылает его боту в каждом запросе.",
    "telegram_bot_token": "Telegram → @BotFather → /newbot → токен бота.",
    "telegram_chat_id": "Напишите боту любое сообщение, затем откройте "
                        "https://api.telegram.org/bot<TOKEN>/getUpdates и возьмите chat.id. "
                        "Бот отвечает только этому чату.",
    "anthropic_api_key": "console.anthropic.com → API Keys.",
    "garmin_personal_api_key": "connect.garmin.com/app/settings/personalApiToken → Create Key. "
                               "Ключ показывается один раз; срок 30/90/180 дней или 1 год.",
}


def run(cmd: list[str], *, input_text: str | None = None, check: bool = True, capture: bool = True) -> str:
    res = subprocess.run(cmd, input=input_text, text=True, capture_output=capture)
    if check and res.returncode != 0:
        sys.exit(f"Команда завершилась с ошибкой: {' '.join(cmd[:4])} …\n{res.stderr.strip()}")
    return (res.stdout or "").strip()


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


# ── config ────────────────────────────────────────────────────────────────────
def cmd_config(_args) -> None:
    project = ask("ID проекта GCP", run(["gcloud", "config", "get-value", "project"], check=False) or None)
    region = ask("Регион", "europe-west3")

    print("\nИсточники данных:")
    names = list(SOURCES)
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n:<7} {SOURCES[n]}")
    print("Перечислите номера через запятую в порядке приоритета (первый — главный при дублях).")
    while True:
        picked = [p.strip() for p in ask("Источники", "1").split(",") if p.strip()]
        try:
            chosen = [names[int(p) - 1] for p in picked]
        except (ValueError, IndexError):
            print("Нужны номера из списка.")
            continue
        if chosen and len(set(chosen)) == len(chosen):
            break
        print("Выберите хотя бы один источник без повторов.")

    schedule = ask("Расписание сборщика (cron, UTC)", "15 */4 * * *")

    if TFVARS.exists() and ask(f"{TFVARS.name} уже есть. Перезаписать? (y/N)", "N").lower() != "y":
        sys.exit("Оставил как есть.")
    # The owner's id keys all data and the bot state: keep it if the install already has one.
    old = re.search(r'^owner_user_id\s*=\s*"([^"]+)"', TFVARS.read_text(encoding="utf-8"), re.M) if TFVARS.exists() else None
    owner_user_id = old.group(1) if old else str(uuid.uuid4())
    sources_hcl = ", ".join(f'"{s}"' for s in chosen)
    TFVARS.write_text(
        f'gcp_project_id = "{project}"\n'
        f'gcp_region     = "{region}"\n'
        f'data_sources   = [{sources_hcl}]\n'
        f'sync_schedule  = "{schedule}"\n'
        f'owner_user_id  = "{owner_user_id}" # не меняйте: по нему хранятся все данные\n'
        "# retired_secrets = [] # секреты, которые больше не нужны: остаются, помечаются status=retired\n",
        encoding="utf-8")
    print(f"\n✔ Записан {TFVARS.relative_to(ROOT)} (в .gitignore)")

    bucket = f"{project}-tfstate"
    exists = subprocess.run(["gcloud", "storage", "buckets", "describe", f"gs://{bucket}", "--project", project],
                            capture_output=True).returncode == 0
    if exists:
        print(f"✔ Бакет для state уже есть: gs://{bucket}")
    elif ask(f"Создать бакет для Terraform state gs://{bucket}? (Y/n)", "Y").lower() == "y":
        run(["gcloud", "storage", "buckets", "create", f"gs://{bucket}", "--project", project,
             "--location", region, "--uniform-bucket-level-access", "--public-access-prevention"])
        run(["gcloud", "storage", "buckets", "update", f"gs://{bucket}", "--versioning"])
        print(f"✔ Создан gs://{bucket} (с версионированием)")

    print(f"""
Дальше:
  cd terraform
  terraform init -backend-config="bucket={bucket}"
  terraform apply -target=google_project_service.apis -target=google_artifact_registry_repository.repo -target=module.secrets
  python3 ../scripts/setup.py secrets
  # соберите и загрузите образы (docs/setup.md, шаг 4), затем:
  terraform apply
  python3 ../scripts/setup.py webhook""")


# ── secrets ───────────────────────────────────────────────────────────────────
def tf_output(name: str):
    return json.loads(run(["terraform", f"-chdir={TF_DIR}", "output", "-json", name]))


def project_id() -> str:
    m = re.search(r'gcp_project_id\s*=\s*"([^"]+)"', TFVARS.read_text(encoding="utf-8")) if TFVARS.exists() else None
    if not m:
        sys.exit("Нет terraform/terraform.tfvars — сначала: python3 scripts/setup.py config")
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
            print(f"✔ {name}: значение уже задано (обновить: --update {name})")
            continue
        print(f"\n{name}: {SECRET_HELP.get(name, '')}")
        if name in GENERATED:
            run(["gcloud", "secrets", "versions", "add", name, "--project", project, "--data-file=-"],
                input_text=secrets.token_urlsafe(32))
            print(f"  ✔ {name}: сгенерирован и сохранён (значение не показывается)")
            continue
        value = getpass.getpass("  значение (ввод скрыт, пусто — пропустить): ").strip()
        if not value:
            print("  пропущено")
            continue
        run(["gcloud", "secrets", "versions", "add", name, "--project", project, "--data-file=-"], input_text=value)
        print(f"  ✔ {name}: новая версия добавлена")


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
    print("✔ webhook настроен" if body.get("ok") else f"Telegram ответил: {body.get('description')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config", help="проект, регион, источники данных").set_defaults(fn=cmd_config)
    p = sub.add_parser("secrets", help="заполнить значения секретов")
    p.add_argument("--update", nargs="*", default=[], help="перезаписать эти секреты (например, при новом ключе)")
    p.set_defaults(fn=cmd_secrets)
    sub.add_parser("webhook", help="настроить webhook Telegram").set_defaults(fn=cmd_webhook)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
