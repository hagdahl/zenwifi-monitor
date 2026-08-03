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
import hashlib
import json
import shutil
import socket
import ssl
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
INSECURE_CONFIRMATION = "I ACCEPT INSECURE HTTP"


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


def ask(prompt: str) -> str:
    """Prompt a person, or refuse when there is nobody there to answer.

    Blocking on stdin in a non-interactive context is not a stall, it is a
    hang: a script, a CI step or a provisioning run waits for ever with no
    output explaining why. Refusing loudly and naming the flag that would have
    supplied the value is always better than waiting.
    """
    def refuse():
        raise SystemExit(
            f"This step needs an answer to: {prompt}\n"
            "There is nobody to ask: standard input is not interactive. "
            "Supply the value on the command line instead; see --help.")

    # Two checks, because neither is sufficient on its own. isatty() is a hint
    # and it lies in both directions: a Windows shell can report a terminal
    # where no input will ever arrive, which is how this was found. The EOF is
    # the fact, so it is caught as well.
    if not sys.stdin or not sys.stdin.isatty():
        refuse()
    try:
        return input(prompt).strip()
    except EOFError:
        refuse()


def probe_tls(host: str, port: int, timeout: float = 10.0) -> dict | None:
    """Complete a TLS handshake and describe the peer, or return None.

    The certificate is deliberately NOT validated, and nothing here should be
    read as saying otherwise. A home router almost always presents a self-signed
    certificate, and refusing it would push every operator towards plain HTTP,
    which is the outcome this check exists to prevent. What the handshake proves
    is that the transport is encrypted, and nothing more: an interceptor between
    this host and the router would complete it just as happily.

    What makes that gap closable is the fingerprint. It is recorded in the
    configuration at `--init` and compared before every restart, so a certificate
    that changes is at least visible — trust on first use rather than no trust
    at all. This mirrors scripts/Setup-RouterConfig.ps1 on Windows.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as secure:
                certificate = secure.getpeercert(binary_form=False) or {}
                der = secure.getpeercert(binary_form=True)
                return {"protocol": secure.version(),
                        "cipher": (secure.cipher() or ("", "", 0))[0],
                        "subject": str(certificate.get("subject", "not presented")),
                        "fingerprint_sha256": hashlib.sha256(der).hexdigest() if der else ""}
    except (OSError, ssl.SSLError, ValueError):
        return None


def initialise(args) -> int:
    """Write a first configuration. Dry run unless --apply, dry-run mode always."""
    target = args.config or (ROOT / "config.local.json")
    if target.exists():
        print(f"{target} already exists. Use --migrate to bring it up to date; "
              f"--init never overwrites a configuration.", file=sys.stderr)
        return 2

    example = load_json(ROOT / "config.example.json")
    host = args.router_host or ask("Router IP address or hostname: ")
    if not host:
        print("A router host is required.", file=sys.stderr)
        return 2
    port = args.management_port
    state = args.state_database or ask("Absolute path for the SQLite database: ")
    logs = args.log_directory or ask("Absolute local log directory: ")
    # Asked for because the router library selects behaviour by model and a
    # configuration without one is a configuration somebody has to come back to.
    # It is NOT required by validate_config, whatever this comment used to say:
    # the watchdog suite's own valid fixture carries no model and passes. That
    # claim survived into the operator-facing error below as well, and is A-13.
    model = args.router_model or ask("Router model: ")
    if not state or not logs:
        print("Both a database path and a log directory are required.", file=sys.stderr)
        return 2
    if not model:
        print("A router model is required by this setup command, so the file it "
              "writes is complete.", file=sys.stderr)
        return 2

    result = PROBE_TLS(host, port)
    print(f"TLS available at {host}:{port} (certificate NOT validated): {bool(result)}")
    if result:
        print(f"  negotiated protocol: {result['protocol']}")
        print(f"  cipher: {result['cipher']}")
        print(f"  certificate subject: {result['subject']}")
        print(f"  certificate SHA-256: {result.get('fingerprint_sha256', '')}")
        print("  This fingerprint is recorded and compared before every restart. "
              "Nothing verified the certificate itself; a change is what you will "
              "be told about.")

    use_tls = True
    if not result:
        print(f"WARNING: no TLS handshake at {host}:{port}. Do not fall back to plain "
              f"HTTP as a convenience. Enable or repair HTTPS in the router's "
              f"administration interface and run this again.", file=sys.stderr)
        if not args.allow_insecure_http:
            print("Refusing to write a router configuration with no TLS at all. "
                  "Nothing here verifies a certificate — an encrypted transport is "
                  "the bar, and it was not met. Use --allow-insecure-http only "
                  "after accepting the risk.", file=sys.stderr)
            return 1
        # An explicit flag is not enough. Typing the sentence is the point: it
        # makes an unencrypted credential path a deliberate act rather than a
        # flag somebody copied out of a forum post.
        typed = ask(f"Type {INSECURE_CONFIRMATION} to allow unencrypted router traffic: ")
        if typed != INSECURE_CONFIRMATION:
            print("Not confirmed exactly. Nothing was written.", file=sys.stderr)
            return 1
        print("WARNING: insecure HTTP authorised. Credentials and router traffic will "
              "have no transport protection.", file=sys.stderr)
        use_tls = False

    router = {"host": host, "management_port": port, "use_tls": use_tls, "model": model}
    fingerprint = result.get("fingerprint_sha256", "") if result else ""
    if use_tls and fingerprint:
        # Trust on first use. Recorded now so a later change is visible; the
        # monitor warns rather than refuses, because a self-signed certificate
        # being renewed must not silence monitoring. The key is written only
        # when there is a real value: an empty string would be a placeholder,
        # and the monitor's own validator rejects those. Absent means "not
        # recorded yet", and the first run that reaches the router records it.
        router["tls_fingerprint_sha256"] = fingerprint
    elif not use_tls:
        # `not use_tls` and not merely "no fingerprint". The seventh review round
        # found that this branch also caught a TLS install whose handshake
        # completed but yielded no certificate bytes, and wrote the
        # acknowledgement into a `use_tls: true` configuration. Flipping the flag
        # to false by hand then passed the monitor's validator with no typed
        # confirmation ever given — defeating, for the life of that install, the
        # very guard the next line claims to set up.
        #
        # The monitor refuses a plain-HTTP configuration unless this is present,
        # so the typed confirmation guards every later run and not only this one.
        router["insecure_http_acknowledged"] = True

    config = {
        "config_version": project_version(),
        "paths": {"log_directory": logs, "state_database": state},
        "router": router,
        "monitor": dict(example["monitor"]),
        # Empty rather than the template's "<Notion data source ID>". A written
        # configuration should contain no placeholder text at all: a value in
        # angle brackets reads like a real setting to anyone skimming, and an
        # empty string fails loudly the moment somebody enables Notion without
        # filling it in.
        "notion": {"enabled": False,
                   "data_source_id": "",
                   "api_version": example.get("notion", {}).get(
                       "api_version", NOTION_API_VERSION_FALLBACK)},
        # Never anything else. A first configuration that could restart a router
        # the moment a timer fires would make the soak period optional, and the
        # soak is how an operator learns what the monitor would have done.
        "execution_mode": "dry-run",
        "outbox": dict(example["outbox"]),
        "health": dict(example["health"]),
    }

    print(f"Execution mode: {config['execution_mode']} (always, for a new configuration)")
    print(f"Notion logging: {config['notion']['enabled']}")
    print(f"Router transport: {'TLS' if use_tls else 'PLAIN HTTP, authorised explicitly'}")

    if not args.apply:
        print(f"Dry run. {target} was not created. Re-run with --apply to write it.")
        return 0

    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {target}.")
    print("Store credentials next, then soak in dry-run before enabling execution.")
    return validate(target)


def accept_router_certificate(args) -> int:
    """Record the certificate the router is presenting now.

    This is the deliberate half of trust on first use. The monitor reports a
    changed certificate and keeps working; resolving the report means a person
    deciding the new one is legitimate, and this writes that decision down. It
    contacts the router and nothing else — no credential is read and no
    execution mode is touched.
    """
    target = args.config or default_config_path()
    if not target.exists():
        print(f"{target} does not exist.", file=sys.stderr)
        return 2
    config = load_json(target)
    router = config.get("router")
    if not isinstance(router, dict) or not router.get("host"):
        print("The configuration has no router host.", file=sys.stderr)
        return 2
    if not router.get("use_tls", True):
        print("This configuration uses plain HTTP by explicit acknowledgement, so "
              "there is no certificate to record.", file=sys.stderr)
        return 2
    port = router.get("management_port", router.get("https_port", 8443))
    result = PROBE_TLS(router["host"], port)
    if not result:
        print(f"No TLS handshake at the configured router and port. Nothing was "
              f"changed.", file=sys.stderr)
        return 1
    previous = router.get("tls_fingerprint_sha256")
    fingerprint = result.get("fingerprint_sha256", "")
    print(f"  negotiated protocol: {result['protocol']}")
    print(f"  certificate subject: {result['subject']}")
    print(f"  certificate SHA-256: {fingerprint}")
    if previous and previous != fingerprint:
        print(f"  replaces: {previous}")
    print("Nothing verified this certificate. Recording it means you have decided "
          "it is the router's.")
    if not args.apply:
        print(f"Dry run. {target} was not changed. Re-run with --apply to record it.")
        return 0
    router["tls_fingerprint_sha256"] = fingerprint
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded in {target}.")
    print("The stored comparison value in the database is refreshed by the next "
          "monitoring run that reaches the router.")
    return 0


def default_config_path() -> Path:
    project_local = ROOT / "config.local.json"
    system_wide = Path("/etc/zenwifi-monitor/config.json")
    if project_local.exists() or not system_wide.exists():
        return project_local
    return system_wide


# Substituted by the suite so the TLS posture can be exercised without a
# server. Production code always calls the real probe.
PROBE_TLS = probe_tls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path,
                        help="Path to the local configuration. Defaults to config.local.json "
                             "beside the project, or /etc/zenwifi-monitor/config.json when that "
                             "exists and the project-local file does not.")
    parser.add_argument("--init", action="store_true", dest="init",
                        help="Create a first configuration. Never overwrites one, and always "
                             "writes execution_mode dry-run.")
    parser.add_argument("--router-host", help="Skip the prompt for the router address.")
    parser.add_argument("--router-model", default="",
                        help="Router model. Required; prompted for when omitted.")
    parser.add_argument("--management-port", type=int, default=8443,
                        help="Router management port. Default 8443.")
    parser.add_argument("--state-database", help="Skip the prompt for the database path.")
    parser.add_argument("--log-directory", help="Skip the prompt for the log directory.")
    parser.add_argument("--allow-insecure-http", action="store_true",
                        help="Last resort. Requires typing a confirmation sentence.")
    parser.add_argument("--migrate", action="store_true",
                        help="Bring an earlier configuration up to the current schema.")
    parser.add_argument("--validate", action="store_true",
                        help="Check a configuration against the monitor's own predicate.")
    parser.add_argument("--discover-gateway", action="store_true", dest="discover",
                        help="Print the default gateway as a candidate for router.host.")
    parser.add_argument("--enable-notion", action="store_true",
                        help="Turn optional Notion logging on. Never implied.")
    parser.add_argument("--accept-router-certificate", action="store_true",
                        dest="accept_certificate",
                        help="Record the certificate the router is presenting now, "
                             "resolving a reported change. Contacts the router only.")
    parser.add_argument("--apply", action="store_true",
                        help="Write the result. Without it nothing on disk changes.")
    args = parser.parse_args()

    if args.discover:
        return discover_gateway()
    if args.init:
        return initialise(args)
    if args.accept_certificate:
        return accept_router_certificate(args)
    if not args.migrate and not args.validate:
        parser.error("Choose --init, --migrate, --validate, --discover-gateway or "
                     "--accept-router-certificate.")

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
