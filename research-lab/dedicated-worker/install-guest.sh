#!/usr/bin/env bash
# Trusted Ubuntu 24.04 guest bootstrap, invoked from a fresh root-owned, exact fork checkout.
set -Eeuo pipefail
if ! declare -F provision_error >/dev/null; then
    provision_stage() { SF_STAGE=$1; printf 'stage=%s\n' "$1" >&2; }
    provision_error() { printf 'failure stage=%s exit=%s file=%s line=%s command=%s\n' "$SF_STAGE" "$@" >&2; }
fi
trap 'provision_error "$?" "${BASH_SOURCE[0]}" "$LINENO" "$BASH_COMMAND"' ERR
provision_stage "guest preflight"
umask 022
[[ $EUID == 0 && $# == 1 && $1 =~ ^[a-f0-9]{40}$ ]]
pin=$1
repo=https://github.com/ShadyF4RR/Stockfish.git
fc_repo=https://github.com/Disservin/fastchess.git
fc_pin=894616028492ae6114835195f14a899f6fa237d3
book_sha=421a4efcddb95c3e70b1f9ad6abbeab1edcb6ab8e9ae743d94c42a5d78a6e82a
# shellcheck source=/dev/null
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]]
[[ $(cat /proc/1/comm) == systemd && -f /sys/fs/cgroup/cgroup.controllers ]]
provision_stage "distro-local interop and Windows mounts"
bash "$(dirname -- "$0")/check-wsl-isolation.sh"
provision_stage "verified disabled distro interop and Windows drive automount"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
src=$(git -C "$(dirname -- "$0")" rev-parse --show-toplevel)
[[ $(stat -c %u "$src") == 0 && $(git -C "$src" remote get-url origin) == "$repo" ]]
[[ $(git -C "$src" rev-parse HEAD) == "$pin" && -z $(git -C "$src" status --porcelain) ]]
[[ -f "$src/research-lab/dedicated-worker/worker.py" ]]
export DEBIAN_FRONTEND=noninteractive
provision_stage "worker package installation"
apt-get update
apt-get install -y --no-install-recommends ca-certificates git curl python3 build-essential expect bubblewrap util-linux time gh

provision_stage "control release checkout"
install -d -o root -g root -m 0755 /opt/stockfish-lab-releases /usr/local/libexec
release=/opt/stockfish-lab-releases/$pin
tmp=$(mktemp -d /opt/.stockfish-provision.XXXXXXXX)
trap 'rm -rf -- "$tmp"' EXIT
if [[ ! -d $release ]]; then
    git clone --quiet --no-local --no-checkout -- "$src" "$tmp/control"
    git -C "$tmp/control" remote set-url origin "$repo"
    git -C "$tmp/control" checkout -q --detach "$pin"
    chmod -R go-w "$tmp/control"
    mv -T -- "$tmp/control" "$release"
fi
[[ $(git -C "$release" rev-parse HEAD) == "$pin" && -z $(git -C "$release" status --porcelain) ]]
[[ ! -e /opt/stockfish-lab || -L /opt/stockfish-lab ]]

provision_stage "Fastchess Git checkout"
if [[ ! -d /opt/stockfish-fastchess ]]; then
    git init --quiet "$tmp/fastchess"
    git -C "$tmp/fastchess" remote add origin "$fc_repo"
    git -C "$tmp/fastchess" fetch --quiet --no-tags --depth=1 origin "$fc_pin"
    git -C "$tmp/fastchess" checkout -q --detach "$fc_pin"
    # Compile the pinned dependency with an unprivileged identity, then freeze the full checkout.
    chmod 0711 "$tmp"
    chown -R nobody:nogroup "$tmp/fastchess"
    provision_stage "Fastchess compilation"
    runuser -u nobody -- make -C "$tmp/fastchess" -j2
    chown -R root:root "$tmp/fastchess"
    chmod -R go-w "$tmp/fastchess"
    mv -T -- "$tmp/fastchess" /opt/stockfish-fastchess
fi
provision_stage "Fastchess verification"
fc=/opt/stockfish-fastchess
[[ $(git -C "$fc" remote get-url origin) == "$fc_repo" && $(git -C "$fc" rev-parse HEAD) == "$fc_pin" &&
   -z $(git -C "$fc" diff --name-only HEAD) && -x "$fc/fastchess" ]]
printf '%s  %s\n' "$book_sha" "$fc/app/tests/data/openings.epd" | sha256sum -c -

provision_stage "service configuration"
if ! id sfworker >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/stockfish-worker --no-create-home --shell /usr/sbin/nologin sfworker
fi
[[ $(id -u sfworker) != 0 && $(id -Gn sfworker) == sfworker ]]
runuser -u sfworker -- test -r "$release/research-lab/dedicated-worker/worker.py"
runuser -u sfworker -- test -x "$fc/fastchess"
runuser -u sfworker -- test ! -w "$release"
runuser -u sfworker -- test ! -w "$fc/fastchess"
install -d -m 0700 -o root -g root /etc/stockfish-worker
install -d -m 0700 -o sfworker -g sfworker /var/lib/stockfish-worker

cat > "$tmp/set-credential" <<'CREDENTIAL'
#!/bin/sh
set -eu
umask 077
test "$(id -u)" = 0
dir=/etc/stockfish-worker
file=$(mktemp "$dir/.github-token.XXXXXXXX")
trap 'rm -f -- "$file"' EXIT
head -c 4097 > "$file"
bytes=$(wc -c < "$file")
test "$bytes" -ge 20 && test "$bytes" -le 4096
# The sender supplies ASCII without a newline. Keep all token bytes off argv and stdout.
test "$(LC_ALL=C tr -d '[:graph:]' < "$file" | wc -c)" -eq 0
mv -fT -- "$file" "$dir/github-token"
CREDENTIAL
install -m 0700 -o root -g root "$tmp/set-credential" /usr/local/libexec/stockfish-worker-set-credential

cat > "$tmp/preflight" <<'PREFLIGHT'
#!/bin/sh
set -eu
test "$(id -un)" = sfworker
test ! -r /etc/stockfish-worker/github-token
test -s "${CREDENTIALS_DIRECTORY:?}/github-token"
grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status
grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status
# systemd may put control processes in a sibling subgroup; test the declared service root.
group=$(systemctl show stockfish-worker.service --property=ControlGroup --value)
case "$group" in /system.slice/stockfish-worker.service) ;; *) exit 1;; esac
probe=$(mktemp -d "/sys/fs/cgroup$group/sf-preflight.XXXXXXXX")
trap 'rmdir "$probe"' EXIT
test -w "$probe/cgroup.procs"
printf '1' > "$probe/cgroup.kill"
PREFLIGHT
install -m 0755 -o root -g root "$tmp/preflight" /usr/local/libexec/stockfish-worker-preflight

cat > "$tmp/stockfish-worker.service" <<'UNIT'
[Unit]
Description=Dedicated Stockfish research worker
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=sfworker
Group=sfworker
WorkingDirectory=/opt/stockfish-lab
StateDirectory=stockfish-worker
StateDirectoryMode=0700
UMask=0077
LoadCredential=github-token:/etc/stockfish-worker/github-token
ExecStartPre=/bin/bash /opt/stockfish-lab/research-lab/dedicated-worker/check-wsl-isolation.sh
ExecStartPre=/usr/local/libexec/stockfish-worker-preflight
ExecStartPre=/usr/bin/bwrap --unshare-user --unshare-pid --unshare-net --die-with-parent --new-session --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 -- /usr/bin/true
ExecStart=/usr/bin/python3 -I /opt/stockfish-lab/research-lab/dedicated-worker/worker.py poll --state /var/lib/stockfish-worker
Restart=always
RestartSec=30
KillMode=control-group
TimeoutStopSec=45
CPUAccounting=yes
CPUQuota=800%
MemoryAccounting=yes
MemoryMax=10G
MemorySwapMax=0
TasksMax=512
Delegate=yes
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
PrivateTmp=yes
InaccessiblePaths=-/run/WSL
RestrictAddressFamilies=~AF_VSOCK
ProtectHome=yes
ProtectSystem=strict
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectKernelTunables=yes
LockPersonality=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
UNIT
install -m 0644 -o root -g root "$tmp/stockfish-worker.service" /etc/systemd/system/stockfish-worker.service
ln -s "$release" "$tmp/current"
mv -fT -- "$tmp/current" /opt/stockfish-lab
systemd-analyze verify /etc/systemd/system/stockfish-worker.service
systemctl daemon-reload
# The host activates the service after successfully installing the root-only credential.
printf '%s\n' "$pin" > /etc/stockfish-worker/control.sha
