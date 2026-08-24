#!/usr/bin/env bash
# Printorian — host readiness check.
#
# Runs on the farm server (the box that will run the compose stack) before first
# boot, and again after any change to its disks or network. It checks the things
# the deployment assumes and prints one line per check, tagged [PASS]/[WARN]/[FAIL],
# then a summary and the concrete fixes for whatever failed.
#
# It is a pre-flight for docs/RUNBOOK-FIRST-BOOT.md, not a replacement. A host that
# scores 0 FAIL here is ready to have deploy/compose.prod.yml brought up; the ten
# minutes of first boot (provision the owner, set the real rates) come after.
#
# Usage:
#
#     sudo bash deploy/readiness-check.sh
#
# Every threshold is a tunable so a larger or smaller farm can raise or lower it:
#
#     PRINTORIAN_REPO_DIR=/srv/printorian/repo   checkout location
#     PRINTORIAN_BACKUP_ROOT=/mnt/backup         where WAL + dumps live (ADR-0019)
#     PRINTORIAN_PROBE_PRINTER=192.168.29.10     probe MQTT:8883 / FTPS:990 here
#     PRINTORIAN_MIN_RAM_GB=8  PRINTORIAN_MIN_CPUS=2  etc.
#
# Exit code: 0 when nothing FAILed (warnings are allowed), 1 when something
# FAILed. That makes it usable as a gate in the Ansible role that provisions the
# box (INFRASTRUCTURE Stage 2): a failed run stops the play rather than deploying
# onto a host that is missing a disk or a secret.
#
# Deliberately NOT 'set -e' and NOT 'set -o pipefail': this script's job is to run
# every check and count the failures, not to die on the first one. Where a
# pipeline's exit status is read, it is read in an 'if', and the status wanted is
# the one of the command that matters — never the tail of a pipe-to-tail (the
# repo's own trap, see the root CLAUDE.md).

set -u

# ---------------------------------------------------------------- tunables
REPO_DIR="${PRINTORIAN_REPO_DIR:-/srv/printorian/repo}"
COMPOSE_REL="${PRINTORIAN_COMPOSE_FILE:-deploy/compose.prod.yml}"
BACKUP_ROOT="${PRINTORIAN_BACKUP_ROOT:-/mnt/backup}"
FARM_TZ="${PRINTORIAN_FARM_TIMEZONE:-Europe/Moscow}"
PRINTER_PROBE="${PRINTORIAN_PROBE_PRINTER:-}"

MIN_RAM_GB="${PRINTORIAN_MIN_RAM_GB:-8}"
MIN_CPUS="${PRINTORIAN_MIN_CPUS:-2}"
MIN_ROOT_FREE_GB="${PRINTORIAN_MIN_ROOT_FREE_GB:-20}"
MIN_BACKUP_FREE_GB="${PRINTORIAN_MIN_BACKUP_FREE_GB:-50}"

# ---------------------------------------------------------------- plumbing
PASS=0; WARN=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "[PASS] $*"; }
warn() { WARN=$((WARN+1)); echo "[WARN] $*"; }
fail() { FAIL=$((FAIL+1)); echo "[FAIL] $*"; }
info() { echo "[INFO] $*"; }
section() { echo; echo "== $* =="; }
have() { command -v "$1" >/dev/null 2>&1; }

ENV_FILE=""

# ================================================================ 1. OS
section "1. Operating system"

if [ ! -r /etc/os-release ]; then
  fail "/etc/os-release is missing — this does not look like a Linux system"
else
  . /etc/os-release 2>/dev/null || true
  case "${ID:-}" in
    ubuntu|debian)
      ok "OS is ${PRETTY_NAME:-$ID} — the deployment targets Debian stable / Ubuntu LTS"
      ;;
    *)
      warn "OS is ${PRETTY_NAME:-$ID} — untested; the runbook assumes Debian/Ubuntu tooling"
      ;;
  esac
fi

[ "$(uname -s)" = "Linux" ]   && ok "kernel is Linux $(uname -r)"   || fail "kernel is $(uname -s) — Docker and the systemd units assume Linux"

case "$(uname -m)" in
  x86_64|aarch64) ok "architecture $(uname -m) matches the published images" ;;
  *) warn "architecture $(uname -m) — images are built for x86_64/arm64; expect local builds" ;;
esac

if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then
  ok "PID 1 is systemd — required by deploy/systemd/*.service and the timers"
else
  fail "PID 1 is not systemd — the provided units will not run; install a systemd distribution"
fi

# ================================================================ 2. Resources
section "2. CPU, RAM and disk"

ram_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
ram_gb=$(( ram_kb / 1048576 ))
if [ "$ram_gb" -ge "$MIN_RAM_GB" ]; then
  ok "RAM: $ram_gb GiB (>= $MIN_RAM_GB recommended)"
elif [ "$ram_gb" -ge 4 ]; then
  warn "RAM: $ram_gb GiB — workable, but derive the PostgreSQL sizes from it in the runbook; more is better"
else
  fail "RAM: $ram_gb GiB — too small; PostgreSQL alone wants several GiB of shared_buffers + cache"
fi

ncpu=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
if [ "$ncpu" -ge "$MIN_CPUS" ]; then
  ok "CPUs: $ncpu (>= $MIN_CPUS recommended — API + workers + mesh analysis in a thread pool)"
else
  warn "CPUs: $ncpu — mesh analysis runs on a bounded thread pool; fewer cores means slower quotes"
fi

docker_root=""
if have docker && docker info >/dev/null 2>&1; then
  docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
fi
[ -n "$docker_root" ] || docker_root="/var/lib/docker"
data_dir="$docker_root"
[ -d "$data_dir" ] || data_dir="/"

data_free_kb=$(df -Pk "$data_dir" 2>/dev/null | awk 'NR==2{print $4}')
data_free_gb=$(( data_free_kb / 1048576 ))
if [ "$data_free_gb" -ge "$MIN_ROOT_FREE_GB" ]; then
  ok "data disk ($data_dir): $data_free_gb GiB free"
elif [ "$data_free_gb" -ge 10 ]; then
  warn "data disk ($data_dir): only $data_free_gb GiB free — telemetry retention and the object store will grow into this"
else
  fail "data disk ($data_dir): $data_free_gb GiB free — grow it before first boot"
fi

if mountpoint -q "$BACKUP_ROOT" 2>/dev/null; then
  root_dev=$(df -P / 2>/dev/null | awk 'NR==2{print $1}')
  bak_dev=$(df -P "$BACKUP_ROOT" 2>/dev/null | awk 'NR==2{print $1}')
  if [ -n "$bak_dev" ] && [ "$bak_dev" != "$root_dev" ]; then
    ok "backup root $BACKUP_ROOT is on a separate device ($bak_dev) — the ADR-0019 separation"
    bak_free_kb=$(df -Pk "$BACKUP_ROOT" 2>/dev/null | awk 'NR==2{print $4}')
    bak_free_gb=$(( bak_free_kb / 1048576 ))
    if [ "$bak_free_gb" -ge "$MIN_BACKUP_FREE_GB" ]; then
      ok "backup disk: $bak_free_gb GiB free"
    else
      warn "backup disk: only $bak_free_gb GiB free — compressed WAL + nightly dumps + weekly base backups will fill it"
    fi
  else
    fail "$BACKUP_ROOT is mounted but on the same device as / — ADR-0019 requires a second physical disk"
  fi
else
  warn "$BACKUP_ROOT is not a mount point yet — attach a second physical disk there (deploy/systemd/README.md, RUNBOOK-BACKUP-RESTORE)"
fi

# ================================================================ 3. Clock
section "3. Clock and timezone"

if have timedatectl; then
  if timedatectl show -p NTPSynchronized 2>/dev/null | grep -q '=yes'; then
    ok "NTP synchronised — the scheduler, rollups and WAL timelines all depend on an honest clock"
  else
    warn "NTP not confirmed synchronised — check systemd-timesyncd or chrony; a drifting clock corrupts rollups"
  fi
  tz=$(timedatectl show -p Timezone --value 2>/dev/null || true)
  if [ "$tz" = "$FARM_TZ" ]; then
    ok "host timezone is $FARM_TZ (matches the farm default)"
  elif [ -n "$tz" ]; then
    warn "host timezone is $tz; the farm defaults to $FARM_TZ — systemd timers fire on host-local time, so set it deliberately"
  fi
else
  warn "timedatectl unavailable — cannot verify clock sync or timezone"
fi

# ================================================================ 4. Tooling
section "4. Host tooling (native) — what is NOT containerised"

for cmd in docker git curl; do
  if have "$cmd"; then ok "$cmd is installed"; else fail "$cmd is missing — install it on the host"; fi
done

if have docker; then
  if docker info >/dev/null 2>&1; then
    ok "docker daemon is running (server $(docker version --format '{{.Server.Version}}' 2>/dev/null))"
  else
    fail "docker daemon is not running or is not reachable — systemctl enable --now docker"
  fi
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose plugin present (v$(docker compose version --short 2>/dev/null))"
  else
    fail "docker compose plugin missing — install docker-compose-plugin, not the legacy docker-compose"
  fi
fi

if [ "$(id -u)" -eq 0 ]; then
  ok "running as root — can drive docker and systemctl directly"
elif [[ " $(id -nG) " == *" docker "* ]]; then
  ok "current user is in the docker group"
else
  warn "not root and not in the docker group — use sudo, or usermod -aG docker <user>"
fi

if command -v dpkg >/dev/null 2>&1 && dpkg -s unattended-upgrades >/dev/null 2>&1; then
  ok "unattended-upgrades installed — kernel/openssl patches arrive without a person"
else
  warn "unattended-upgrades not installed — schedule security patching (deferred while a job prints)"
fi

if have ufw && ufw status 2>/dev/null | grep -q 'Status: active'; then
  ok "ufw is active"
elif have nft && nft list ruleset 2>/dev/null | grep -q .; then
  ok "nftables ruleset is present"
else
  warn "no active firewall detected — ensure 8080/8081 are LAN-only and postgres/redis stay unpublished"
fi

# ================================================================ 5. Runtime smoke test
section "5. Docker runtime smoke test"

if have docker && docker info >/dev/null 2>&1; then
  if docker image inspect hello-world >/dev/null 2>&1; then
    if docker run --rm hello-world >/dev/null 2>&1; then
      ok "ran a container successfully"
    else
      fail "docker run failed — check storage driver and daemon state"
    fi
  else
    info "no cached hello-world image — skipping live run (a pull needs internet); docker run --rm hello-world to confirm"
  fi
fi

# ================================================================ 6. Deployment layout
section "6. Deployment layout and secrets"

compose_full="$REPO_DIR/$COMPOSE_REL"
if [ -f "$compose_full" ]; then
  ok "compose file present: $compose_full"
else
  fail "compose file missing at $compose_full — clone the repository into $REPO_DIR"
fi

for candidate in "$REPO_DIR/deploy/.env" "$REPO_DIR/.env"; do
  if [ -f "$candidate" ]; then ENV_FILE="$candidate"; break; fi
done
if [ -n "$ENV_FILE" ]; then
  ok ".env present at $ENV_FILE"
else
  warn ".env not found beside the compose file — the stack will not start until secrets are rendered (SOPS, INFRASTRUCTURE §4.4)"
fi

env_have() {
  [ -n "${!1:-}" ] && return 0
  [ -n "$ENV_FILE" ] && grep -qE "^[[:space:]]*${1}=" "$ENV_FILE" 2>/dev/null
}

for var in POSTGRES_PASSWORD PRINTORIAN_SECRET_KEY PRINTORIAN_IMAGE PRINTORIAN_CONSOLE_IMAGE; do
  if env_have "$var"; then ok "$var is set"; else fail "$var is not set (required by deploy/compose.prod.yml)"; fi
done

if env_have PRINTORIAN_SECRET_KEY; then
  val=$(printenv PRINTORIAN_SECRET_KEY 2>/dev/null || grep -E '^PRINTORIAN_SECRET_KEY=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2-)
  case "$val" in
    ""|dev-only-not-a-real-secret) fail "PRINTORIAN_SECRET_KEY is the published dev default — generate a real one and escrow it off-box (ADR-0019 §6)" ;;
    *) ok "PRINTORIAN_SECRET_KEY is not the dev default" ;;
  esac
fi

if env_have PRINTORIAN_IMAGE; then
  img=$(printenv PRINTORIAN_IMAGE 2>/dev/null || grep -E '^PRINTORIAN_IMAGE=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2-)
  case "$img" in
    *@sha256:*) ok "PRINTORIAN_IMAGE is digest-pinned" ;;
    *) warn "PRINTORIAN_IMAGE is not digest-pinned ($img) — pin to @sha256: before production" ;;
  esac
fi

if [ -f "$compose_full" ] && [ -n "$ENV_FILE" ] && have docker && docker info >/dev/null 2>&1; then
  if (cd "$REPO_DIR" && docker compose -f "$COMPOSE_REL" config --quiet >/dev/null 2>&1); then
    ok "compose file validates"
  else
    warn "compose file did not validate — run docker compose -f $COMPOSE_REL config to see the interpolation error"
  fi
fi

if [ -d "$BACKUP_ROOT" ]; then
  ok "backup root exists: $BACKUP_ROOT"
else
  warn "backup root $BACKUP_ROOT does not exist yet — mkdir it and mount the second disk there"
fi

# ================================================================ 7. Network
section "7. Network and ports"

if have ss; then
  for port in 8080 8081; do
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq ":$port$"; then
      warn "port $port is already listening — it collides with the console/storefront bind"
    else
      ok "port $port is free"
    fi
  done
  info "postgres (5432) and redis (6379) are container-internal in production — do not publish them"
else
  warn "ss unavailable — cannot check that 8080/8081 are free (install iproute2)"
fi

if [ -n "$PRINTER_PROBE" ]; then
  for port in 8883 990; do
    if timeout 3 bash -c "exec 3<>/dev/tcp/$PRINTER_PROBE/$port" 2>/dev/null; then
      ok "printer reachable at $PRINTER_PROBE:$port"
    else
      warn "cannot reach $PRINTER_PROBE:$port — printers need MQTT:8883 and FTPS:990 (own VLAN, ADR-0003)"
    fi
  done
else
  info "set PRINTORIAN_PROBE_PRINTER=<printer-ip> to test MQTT:8883 / FTPS:990 reachability"
fi

if timeout 6 curl -fsSL -o /dev/null https://github.com 2>/dev/null; then
  ok "outbound HTTPS works — image pulls from GHCR are possible"
else
  warn "no outbound HTTPS — images must be preloaded or pulled through a proxy"
fi

# ================================================================ 8. systemd units
section "8. systemd units (installed after the layout is in place)"

for unit in printorian.service printorian-ensure.timer printorian-backup.timer printorian-drill.timer; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
      ok "$unit installed and enabled"
    else
      warn "$unit installed but not enabled — systemctl enable --now $unit"
    fi
  else
    info "$unit not installed yet — see deploy/systemd/README.md (install after the checkout and .env exist)"
  fi
done

if [ -x "$REPO_DIR/deploy/reconcile.sh" ]; then
  ok "reconcile.sh is executable"
elif [ -f "$REPO_DIR/deploy/reconcile.sh" ]; then
  warn "reconcile.sh exists but is not executable — chmod +x $REPO_DIR/deploy/reconcile.sh"
else
  info "reconcile.sh not found (it ships with the checkout)"
fi

# ================================================================ 9. Later stages
section "9. Not required for first boot (informational)"

info "cosign      — image signature verification on the farm (INFRASTRUCTURE Stage 4, the deploy loop)"
info "restic      — encrypted off-site backup sync (recipe in RUNBOOK-BACKUP-RESTORE §2)"
info "wireguard   — the farm-dials-out tunnel to the edge VPS (Stage 3; the storefront is rehearsal-only until then)"
info "nut (UPS)   — clean shutdown before the battery dies (INFRASTRUCTURE §5, power control loop)"
info "node_exporter / postgres_exporter / victoriametrics / grafana / loki — observability (Stage 5)"

# ================================================================ summary
section "Summary"

echo "$PASS pass · $WARN warn · $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
  echo
  echo "Readiness NOT met. Fix every [FAIL] above, then re-run. Warnings are acceptable for a first boot but must be decided, not ignored."
  echo "Then follow docs/RUNBOOK-FIRST-BOOT.md, and install the units per deploy/systemd/README.md."
  exit 1
fi

echo
echo "Readiness met. Proceed: install the systemd units, then docs/RUNBOOK-FIRST-BOOT.md (provision owner, set real rates, register a printer)."
if [ "$WARN" -gt 0 ]; then
  echo "Address the $WARN warning(s) above before the farm takes real orders — several are the ADR-0019 backup separation and secret escrow."
fi
exit 0
