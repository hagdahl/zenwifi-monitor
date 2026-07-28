#!/usr/bin/env bash
# ZenWiFi Monitor version: 0.1.0
#
# Debian installer. Mirrors scripts/Install.ps1: it prepares the environment,
# registers the scheduled work, and refuses to enable production execution
# unless asked in as many words.
#
# The two activation gates are the same as on Windows and both are required:
#   1. the unit must carry --execute, which only --enable-execution writes; and
#   2. execution_mode in the local configuration must be "execute".
# A default install therefore cannot restart a router, whatever the timer does.
set -euo pipefail

PREFIX=/opt/zenwifi-monitor
CONFIG_DIR=/etc/zenwifi-monitor
CREDENTIAL_DIR="${CONFIG_DIR}/credentials"
UNIT_DIR=/etc/systemd/system
SERVICE_USER=zenwifi
DROPIN_DIR="${UNIT_DIR}/zenwifi-monitor.service.d"
DROPIN="${DROPIN_DIR}/10-execute.conf"

ENABLE_EXECUTION=0
SET_CREDENTIALS=0
DO_INSTALL=0

usage() {
  cat <<'USAGE'
Usage: install.sh [--install] [--set-credentials] [--enable-execution]

  --install            Create the service account, the environment and the
                       directories, install the units, and enable the timers.
                       Production execution stays off.
  --set-credentials    Encrypt router (and optionally Notion) credentials with
                       systemd-creds. Prompts; nothing is echoed or stored in
                       shell history.
  --enable-execution   Write the drop-in that adds --execute to the watchdog
                       unit. This is one of the two gates; the configuration
                       still decides the other. Refuses without --install
                       having been run first.

With no option this prints what would happen and changes nothing.
USAGE
}

for argument in "$@"; do
  case "$argument" in
    --install) DO_INSTALL=1 ;;
    --set-credentials) SET_CREDENTIALS=1 ;;
    --enable-execution) ENABLE_EXECUTION=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: ${argument}" >&2; usage >&2; exit 2 ;;
  esac
done

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This step needs root. Re-run with sudo." >&2
    exit 1
  fi
}

if [[ "${DO_INSTALL}" -eq 0 && "${SET_CREDENTIALS}" -eq 0 && "${ENABLE_EXECUTION}" -eq 0 ]]; then
  echo "Dry run. Nothing was changed."
  echo "  source           ${SOURCE_DIR}"
  echo "  install prefix   ${PREFIX}"
  echo "  configuration    ${CONFIG_DIR}/config.json"
  echo "  credentials      ${CREDENTIAL_DIR}"
  echo "  service account  ${SERVICE_USER}"
  echo "  units            zenwifi-monitor{,.timer} zenwifi-monitor-health{,.timer}"
  echo
  echo "Production execution is OFF unless --enable-execution is given AND"
  echo "execution_mode in ${CONFIG_DIR}/config.json is \"execute\"."
  echo "Re-run with --install to proceed."
  exit 0
fi

if [[ "${DO_INSTALL}" -eq 1 ]]; then
  require_root
  command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }
  python3 - <<'PYVERSION' || { echo "Python 3.11 or newer is required." >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PYVERSION

  # A dedicated account with no login shell and no home: the monitor needs an
  # identity to own its state, not a user anybody can become.
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
  fi

  install -d -m 0755 "${PREFIX}"
  install -d -m 0750 -o root -g "${SERVICE_USER}" "${CONFIG_DIR}"
  install -d -m 0750 -o root -g "${SERVICE_USER}" "${CREDENTIAL_DIR}"
  install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_USER}" /var/lib/zenwifi-monitor
  install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_USER}" /var/log/zenwifi-monitor

  # A monitor that cannot record a pre-database failure is the failure mode this
  # project keeps meeting, so refuse the install rather than discover it later.
  # The service account has no home and the units set ProtectHome=yes, so this
  # directory is the only place a crash before the database can be written down.
  if ! runuser -u "${SERVICE_USER}" -- test -w /var/log/zenwifi-monitor; then
    echo "The service account cannot write /var/log/zenwifi-monitor. Refusing to install." >&2
    exit 1
  fi

  # NOT cp -a. That preserves the ownership of the clone, and the documented
  # invocation is sudo from a user-owned checkout, so the installed code would
  # end up writable by an ordinary account while systemd runs it as the service
  # user with the router credentials decrypted into the process. Every hardening
  # directive in the unit would still be satisfied, because none of them says
  # anything about who may rewrite the code before it starts.
  #
  # rm -rf first: install(1) overwrites the files it is given, so a module left
  # behind by an older version would survive an upgrade and keep being importable.
  rm -rf "${PREFIX}/src"
  install -d -m 0755 -o root -g root "${PREFIX}/src"
  find "${SOURCE_DIR}/src" -maxdepth 1 -type f -name '*.py' \
    -exec install -m 0644 -o root -g root {} "${PREFIX}/src/" \;
  # configure.py is standard-library only, so it works before the environment
  # exists; it is installed alongside so an operator can validate and migrate
  # the configuration on the target host rather than only at build time.
  install -d -m 0755 -o root -g root "${PREFIX}/scripts"
  install -m 0755 -o root -g root "${SOURCE_DIR}/scripts/configure.py" "${PREFIX}/scripts/configure.py"
  install -m 0644 -o root -g root "${SOURCE_DIR}/VERSION" "${PREFIX}/VERSION"
  install -m 0644 -o root -g root "${SOURCE_DIR}/config.example.json" "${PREFIX}/config.example.json"
  install -m 0644 -o root -g root "${SOURCE_DIR}/requirements.in" "${PREFIX}/requirements.in"
  install -m 0644 -o root -g root "${SOURCE_DIR}/requirements.lock.txt" "${PREFIX}/requirements.lock.txt"

  python3 -m venv "${PREFIX}/venv"
  # --require-hashes: pip refuses the whole set unless every artefact matches a
  # recorded hash, so a substituted distribution fails the install rather than
  # reaching a service that can restart a router.
  "${PREFIX}/venv/bin/python" -m pip install --upgrade pip
  "${PREFIX}/venv/bin/python" -m pip install --require-hashes -r "${PREFIX}/requirements.lock.txt"
  "${PREFIX}/venv/bin/python" -m pip check

  if [[ ! -f "${CONFIG_DIR}/config.json" ]]; then
    install -m 0640 -o root -g "${SERVICE_USER}" \
      "${SOURCE_DIR}/config.example.json" "${CONFIG_DIR}/config.json"
    echo "Wrote a template to ${CONFIG_DIR}/config.json. Edit it before enabling execution."
    echo "Then check it with:"
    echo "  ${PREFIX}/venv/bin/python ${PREFIX}/scripts/configure.py --config ${CONFIG_DIR}/config.json --validate"
  else
    echo "Kept the existing ${CONFIG_DIR}/config.json."
  fi

  install -m 0644 "${SOURCE_DIR}/deploy/debian"/zenwifi-monitor*.service "${UNIT_DIR}/"
  install -m 0644 "${SOURCE_DIR}/deploy/debian"/zenwifi-monitor*.timer "${UNIT_DIR}/"
  systemctl daemon-reload
  systemctl enable --now zenwifi-monitor.timer zenwifi-monitor-health.timer

  echo "Installed. Production execution is OFF."
fi

if [[ "${SET_CREDENTIALS}" -eq 1 ]]; then
  require_root
  command -v systemd-creds >/dev/null || { echo "systemd-creds is required." >&2; exit 1; }
  install -d -m 0750 -o root -g "${SERVICE_USER}" "${CREDENTIAL_DIR}"
  # read -s keeps the value off the screen; passing it to systemd-creds on stdin
  # keeps it off the command line, where it would be visible in the process list.
  encrypt_one() {
    local name="$1" prompt="$2" value
    read -r -s -p "${prompt}: " value; echo
    if [[ -z "${value}" ]]; then
      echo "  skipped ${name} (left unchanged)"
      return 0
    fi
    printf '%s' "${value}" | systemd-creds encrypt --name="${name}" - \
      "${CREDENTIAL_DIR}/${name}.cred"
    chmod 0640 "${CREDENTIAL_DIR}/${name}.cred"
    chown root:"${SERVICE_USER}" "${CREDENTIAL_DIR}/${name}.cred"
    unset value
    echo "  stored ${name}"
  }
  encrypt_one router_username "Router username"
  encrypt_one router_password "Router password"
  encrypt_one notion_token "Notion token (leave empty to skip)"
  echo "Credentials written to ${CREDENTIAL_DIR}."
fi

if [[ "${ENABLE_EXECUTION}" -eq 1 ]]; then
  require_root
  if [[ ! -f "${UNIT_DIR}/zenwifi-monitor.service" ]]; then
    echo "The unit is not installed. Run --install first." >&2
    exit 1
  fi
  install -d -m 0755 "${DROPIN_DIR}"
  cat > "${DROPIN}" <<'DROPIN_BODY'
# ZenWiFi Monitor version: 0.1.0
# Activation gate 1 of 2, written only by install.sh --enable-execution.
# ExecStart is cleared first because systemd appends otherwise, which would run
# the monitor twice per timer firing.
[Service]
ExecStart=
ExecStart=/opt/zenwifi-monitor/venv/bin/python /opt/zenwifi-monitor/src/watchdog.py --config /etc/zenwifi-monitor/config.json --execute
DROPIN_BODY
  chmod 0644 "${DROPIN}"
  systemctl daemon-reload
  echo "Gate 1 is now open: the unit carries --execute."
  echo "Gate 2 is execution_mode in ${CONFIG_DIR}/config.json. Both are required."
fi
