#!/usr/bin/env bash
# Trusted Ubuntu 24.04 guest bootstrap, invoked from a fresh root-owned, exact fork checkout.
set -euo pipefail
umask 022
die() { echo "Stockfish worker installation: $*" >&2; exit 1; }
[[ $EUID == 0 && $# == 1 && $1 =~ ^[a-f0-9]{40}$ ]] || die "run as root with the approved full control commit"
pin=$1
repo=https://github.com/ShadyF4RR/Stockfish.git
fc_repo=https://github.com/Disservin/fastchess.git
fc_pin=894616028492ae6114835195f14a899f6fa237d3
book_sha=421a4efcddb95c3e70b1f9ad6abbeab1edcb6ab8e9ae743d94c42a5d78a6e82a
# shellcheck source=/dev/null
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || die "Ubuntu 24.04 x86-64 is required"
[[ $(cat /proc/1/comm) == systemd && -f /sys/fs/cgroup/cgroup.controllers ]] || die "enable systemd and cgroup v2 in the WSL2 guest"
if awk '$0 ~ / - drvfs / || ($0 ~ / - 9p / && $0 ~ /aname=drvfs/) {found=1} END {exit !found}' /proc/self/mountinfo; then
    die "disable Windows drive automount and restart this distro before installation"
fi
[[ ! -e /proc/sys/fs/binfmt_misc/WSLInterop ]] || die "Windows process interop is still enabled"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
src=$(git -C "$(dirname -- "$0")" rev-parse --show-toplevel)
[[ $(stat -c %u "$src") == 0 && $(git -C "$src" remote get-url origin) == "$repo" ]] || die "unexpected control checkout"
[[ $(git -C "$src" rev-parse HEAD) == "$pin" && -z $(git -C "$src" status --porcelain) ]] || die "control commit mismatch or dirty checkout"
[[ -f "$src/research-lab/dedicated-worker/worker.py" ]] || die "approved revision lacks the dedicated worker"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates git curl python3 build-essential expect bubblewrap util-linux time gh

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
[[ $(git -C "$release" rev-parse HEAD) == "$pin" && -z $(git -C "$release" status --porcelain) ]] || die "existing release differs from approved control"
[[ ! -e /opt/stockfish-lab || -L /opt/stockfish-lab ]] || die "existing unmanaged /opt/stockfish-lab"

if [[ ! -d /opt/stockfish-fastchess ]]; then
    git init --quiet "$tmp/fastchess"
    git -C "$tmp/fastchess" remote add origin "$fc_repo"
    git -C "$tmp/fastchess" fetch --quiet --no-tags --depth=1 origin "$fc_pin"
    git -C "$tmp/fastchess" checkout -q --detach "$fc_pin"
    # Compile the pinned dependency with an unprivileged identity, then freeze the full checkout.
    chmod 0711 "$tmp"
    chown -R nobody:nogroup "$tmp/fastchess"
    runuser -u nobody -- make -C "$tmp/fastchess" -j2
    chown -R root:root "$tmp/fastchess"
    chmod -R go-w "$tmp/fastchess"
    mv -T -- "$tmp/fastchess" /opt/stockfish-fastchess
fi
fc=/opt/stockfish-fastchess
[[ $(git -C "$fc" remote get-url origin) == "$fc_repo" && $(git -C "$fc" rev-parse HEAD) == "$fc_pin" &&
   -z $(git -C "$fc" diff --name-only HEAD) && -x "$fc/fastchess" ]] || die "incorrect Fastchess checkout or binary"
printf '%s  %s\n' "$book_sha" "$fc/app/tests/data/openings.epd" | sha256sum -c -

if ! id sfworker >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/stockfish-worker --no-create-home --shell /usr/sbin/nologin sfworker
fi
[[ $(id -u sfworker) != 0 && $(id -Gn sfworker) == sfworker ]] || die "sfworker must have only its dedicated group"
runuser -u sfworker -- test -r "$release/research-lab/dedicated-worker/worker.py"
runuser -u sfworker -- test -x "$fc/fastchess"
if runuser -u sfworker -- test -w "$release" || runuser -u sfworker -- test -w "$fc/fastchess"; then
    die "worker must not own or write the runtime"
fi
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
