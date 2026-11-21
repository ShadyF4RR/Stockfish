#!/usr/bin/env bash
# Read only. A VM-wide binfmt entry and /run/WSL/1_interop can exist on an isolated distro.
set -euo pipefail

check_policy() {
    # Accept ordinary INI comments, unrelated sections and whitespace. Require
    # unambiguous literal false settings with explicit section membership.
    LC_ALL=C awk '
        function trim(v) { sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v); return v }
        BEGIN {
            wanted["interop.enabled"]=1; wanted["interop.appendwindowspath"]=1
            wanted["automount.enabled"]=1; wanted["automount.mountfstab"]=1
        }
        {
            sub(/\r$/, ""); printable=$0; gsub(/\t/, "", printable)
            if (printable ~ /[^ -~]/) { bad=1; next }
            sub(/#.*/, ""); line=trim($0)
            if (line == "") next
            # Do not interpret a continuation or quoted multiline value differently from WSL.
            if (line ~ /[\\"]/) { bad=1; next }
            if (line ~ /^\[[[:alpha:]][[:alnum:]]*\]$/) {
                section=tolower(substr(line,2,length(line)-2)); next
            }
            if (!section || line !~ /^[[:alpha:]][[:alnum:]]*[ \t]*=/) { bad=1; next }
            i=index(line,"="); key=section "." tolower(trim(substr(line,1,i-1)))
            if (key in wanted) {
                if (++seen[key] != 1 || trim(substr(line,i+1)) != "false") bad=1
            }
        }
        END { for (k in wanted) if (seen[k] != 1) bad=1; exit bad ? 1 : 0 }
    ' "$1"
}

check_mounts() {
    LC_ALL=C awk '
        {
            if (split($0, fields, " - ") != 2 || split(fields[2], fs, " ") < 3) { bad=1; next }
            if (fs[1]=="drvfs" || (fs[1]=="9p" && (fs[2] ~ /^(drvfs|drvfsa)$/ || fs[2] ~ /^[[:alpha:]]:/ || fs[3] ~ /aname=drvfs/))) bad=1
            # WSL 3 also transports Windows drives over virtiofs (GUID shares
            # and the aggregate tag). Read-only WSL system libraries are separate.
            if (fs[1]=="virtiofs" && (fs[2] ~ /^(drvfs|drvfsa)$/ || fs[2] ~ /^[[:alpha:]]:/ ||
                fs[2] ~ /^\{?[[:xdigit:]]+-[[:xdigit:]]+-[[:xdigit:]]+-[[:xdigit:]]+-[[:xdigit:]]+\}?$/)) bad=1
            count++
        } END { exit (!count || bad) ? 1 : 0 }
    ' "$1"
}

verify_isolation() {
    local conf=$1 stamp=$2 mounts=$3
    [[ -f "$conf" && ! -L "$conf" && -r "$conf" && -s "$stamp" ]] || return 21
    check_policy "$conf" || return 22
    # The keeper recorded this generation before terminating just this distro
    # and committed it only after observing a new PID namespace. Reject stale policy.
    local generation
    generation=$(TZ=UTC0 stat -c '%i %z' "$conf" && sha256sum "$conf") || return 23
    cmp -s "$stamp" <(printf '%s\n' "$generation") || return 23
    [[ ! -v WSL_INTEROP ]] || return 24
    check_mounts "$mounts" || return 25
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    verify_isolation /etc/wsl.conf /etc/stockfish-wsl-isolation/applied /proc/self/mountinfo
fi
