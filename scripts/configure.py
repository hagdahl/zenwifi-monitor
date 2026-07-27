#!/usr/bin/env python3
# ZenWiFi Monitor version: 0.1.0
"""Configuration setup, migration and validation, on either platform.

This is the single implementation. `scripts/Migrate-LocalConfig.ps1` is a thin
wrapper that calls it, so the two platforms cannot drift into disagreeing about
what a current configuration looks like — which is exactly what happened to the
retry bound before it was moved into `src/_defaults.py`.

Standard library only, deliberately. Configuration work must be possible before
the environment exists, which is precisely when the third-party dependencies
are not installed yet.

Nothing here contacts the router or Notion, reads a credential, or enables
restart execution. Every mode is a dry run unless `--apply` is given, and
`execution_mode` is never changed once it is present: turning a monitor loose
on a router is a decision for a person, not for a migration.
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Sections whose missing keys are safe to fill from the template. `paths` and
# `router` are excluded on purpose: the template carries placeholders like
# "<router IP address or hostname>" there, and copying one into a live
# configuration would replace a working value with a string that fails at the
# next run, which is a worse outcome than an absent optional key.
FILLABLE_SECTIONS = ("monitor", "outbox", "health")
NOTION_API_VERSION_FALLBACK = "2026-03-11"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def project_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def migrate(config: dict, example: dict, enable_notion: bool) -> tuple[dict, list[str], bool]:
    """Return the migrated configuration, what changed, and the Notion state."""
    changes: list[str] = []
    if "router" not in config:
        raise SystemExit("The local configuration has no router section.")
    config.setdefault("notion", {})

    router = config["router"]
    if "management_port" not in router:
        if "https_port" not in router:
            raise SystemExit("The router section has neither management_port nor https_port.")
        router["management_port"] = router.pop("https_port")
        changes.append("Mapped router.https_port to router.management_port.")
    if "use_tls" not in router:
        router["use_tls"] = True
        changes.append("Set router.use_tls to true.")

    # Only report a change when one actually happens. Announcing "set Notion to
    # disabled" on a configuration that already said so makes every run look
    # like a migration, which hides the one run that really is.
    notion_declared = "enabled" in config["notion"]
    notion_was_enabled = bool(config["notion"].get("enabled", False))
    notion_enabled = notion_was_enabled or enable_notion
    config["notion"]["enabled"] = notion_enabled
    if notion_enabled and not notion_was_enabled:
        changes.append("Enabled optional Notion logging by explicit request.")
    elif not notion_declared:
        changes.append("Set optional Notion logging to disabled.")
    if "api_version" not in config["notion"]:
        config["notion"]["api_version"] = example.get("notion", {}).get(
            "api_version", NOTION_API_VERSION_FALLBACK)
        changes.append("Added the Notion API version from the template.")

    version = project_version()
    if config.get("config_version") != version:
        verb = "Updated" if "config_version" in config else "Added"
        config["config_version"] = version
        changes.append(f"{verb} config_version to {version}.")

    for section in FILLABLE_SECTIONS:
        template = example.get(section)
        if not isinstance(template, dict):
            continue
        if section not in config:
            config[section] = dict(template)
            changes.append(f"Added the {section} section with template defaults.")
            continue
        for key, value in template.items():
            if key not in config[section]:
                config[section][key] = value
                changes.append(f"Added {section}.{key} with the template default.")

    # Only ever added, never changed. An install already in execute mode stays
    # there; one that never said anything starts closed.
    if "execution_mode" not in config:
        config["execution_mode"] = "dry-run"
        changes.append("Set execution_mode to dry-run.")

    return config, changes, notion_enabled


def validate(path: Path) -> int:
    """Run the monitor's own predicate, not a second opinion about it."""
    try:
        from watchdog import validate_config
    except Exception as error:  # pragma: no cover - import failure is the message
        print(f"Could not load the validator: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    try:
        validate_config(load_json(path))
    except Exception as error:
        print(f"INVALID: {error}", file=sys.stderr)
        return 1
    print(f"VALID: {path} satisfies the monitor's own configuration predicate.")
    return 0


def discover_gateway() -> int:
    """Report the default gateway. It is never written; that is a decision."""
    if sys.platform.startswith("linux"):
        if shutil.which("ip") is None:
            print("The 'ip' command is not available.", file=sys.stderr)
            return 1
        completed = subprocess.run(["ip", "-4", "route", "show", "default"],
                                   capture_output=True, text=True, check=False, timeout=10)
        fields = completed.stdout.split()
        if "via" in fields:
            gateway = fields[fields.index("via") + 1]
            print(f"Default gateway: {gateway}")
            print("This is a candidate for router.host. Nothing was written; set it yourself.")
            return 0
        print("No default IPv4 route was found.", file=sys.stderr)
        return 1
    print("Gateway discovery is implemented for Linux here. On Windows use "
          "scripts/Setup-RouterConfig.ps1, which also verifies the TLS handshake.",
          file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path,
                        help="Path to the local configuration. Defaults to config.local.json "
                             "beside the project, or /etc/zenwifi-monitor/config.json when that "
                             "exists and the project-local file does not.")
    parser.add_argument("--migrate", action="store_true",
                        help="Bring an earlier configuration up to the current schema.")
    parser.add_argument("--validate", action="store_true",
                        help="Check a configuration against the monitor's own predicate.")
    parser.add_argument("--discover-gateway", action="store_true", dest="discover",
                        help="Print the default gateway as a candidate for router.host.")
    parser.add_argument("--enable-notion", action="store_true",
                        help="Turn optional Notion logging on. Never implied.")
    parser.add_argument("--apply", action="store_true",
                        help="Write the result. Without it nothing on disk changes.")
    args = parser.parse_args()

    if args.discover:
        return discover_gateway()
    if not args.migrate and not args.validate:
        parser.error("Choose --migrate, --validate or --discover-gateway.")

    config_path = args.config
    if config_path is None:
        project_local = ROOT / "config.local.json"
        system_wide = Path("/etc/zenwifi-monitor/config.json")
        config_path = project_local if project_local.is_file() else system_wide
    if not config_path.is_file():
        print(f"{config_path} does not exist.", file=sys.stderr)
        return 2

    if args.validate and not args.migrate:
        return validate(config_path)

    example_path = ROOT / "config.example.json"
    if not example_path.is_file():
        print(f"{example_path} is missing; it is the source of the defaults.", file=sys.stderr)
        return 2

    config, changes, notion_enabled = migrate(
        load_json(config_path), load_json(example_path), args.enable_notion)

    print("Configuration migration plan:")
    for change in changes or ["No schema changes are required."]:
        print(f"- {change}" if changes else change)
    print(f"Notion logging after migration: {notion_enabled}")
    print("Restart execution after migration: unchanged.")
    print(f"Execution mode after migration: {config['execution_mode']}")

    if not args.apply:
        print("Dry run. Nothing was written. Re-run with --apply to create a backup "
              "and update the file.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = config_path.with_name(f"{config_path.name}.{stamp}.bak")
    shutil.copy2(config_path, backup)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print("Local configuration migrated. No router, Notion or credential action was performed.")
    print(f"Backup created: {backup}")

    if args.validate:
        return validate(config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
