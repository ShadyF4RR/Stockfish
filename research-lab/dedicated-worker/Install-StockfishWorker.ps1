#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ControlSha,
    [Security.SecureString]$WorkerToken,
    [switch]$Resume,
    [switch]$Keeper
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$winAccount = 'StockfishLabSvc'
$taskName = 'Stockfish Research Worker'
$distro = 'StockfishWorker-2404'
$root = Join-Path $env:ProgramData 'StockfishResearchWorker'
$private = Join-Path $root 'Administrator'
$handoff = Join-Path $root 'Handoff'
$inbox = Join-Path $root 'Input'
$tokenFile = Join-Path $inbox 'pending-token.dpapi'
$pinFile = Join-Path $root 'control.sha'
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
$utf8 = New-Object Text.UTF8Encoding($false)
$admins = [Security.Principal.SecurityIdentifier]'S-1-5-32-544'
$system = [Security.Principal.SecurityIdentifier]'S-1-5-18'
Add-Type -AssemblyName System.Security

function Write-PrivateDirectory($Path, $ReaderSid, $ReaderRights) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($admins)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($system, $admins)) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    }
    if ($ReaderSid) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $ReaderSid, $ReaderRights, 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    }
    if ([IO.Directory]::Exists($Path)) {
        $attrs = [IO.File]::GetAttributes($Path)
        $owner = [IO.Directory]::GetAccessControl($Path).GetOwner([Security.Principal.SecurityIdentifier])
        if (($attrs -band [IO.FileAttributes]::ReparsePoint) -or ($owner -ne $admins -and $owner -ne $system)) {
            throw "Refusing an existing directory outside administrator control: $Path"
        }
        [IO.Directory]::SetAccessControl($Path, $acl)
    } else {
        [void][IO.Directory]::CreateDirectory($Path, $acl)
    }
}

function ConvertTo-SecretBytes([Security.SecureString]$Value) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return ,$utf8.GetBytes([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr))
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Write-Envelope([string]$Path, [byte[]]$Bytes) {
    $cipher = [Security.Cryptography.ProtectedData]::Protect(
        $Bytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    [IO.File]::WriteAllBytes($Path, $cipher)
}

function Read-Envelope([string]$Path) {
    return ,([Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes($Path), $null, [Security.Cryptography.DataProtectionScope]::LocalMachine))
}

# The only data copied to the Windows log is the structured output of the
# diagnostic shell below. The token import never enters that shell.
$logPath = Join-Path $handoff 'provisioning.log'
function Write-ProvisionLog([string]$Message) {
    [IO.File]::AppendAllText($logPath, ([DateTime]::UtcNow.ToString('o') + ' ' + $Message + [Environment]::NewLine), $utf8)
}

function Invoke-GuestBytes([string]$Command, [byte[]]$Bytes, [switch]$Diagnostics, [int]$ExpectedExit = -1) {
    # Commands are fixed below. Credential bytes pass only through the child's stdin.
    $entry = New-Object Diagnostics.ProcessStartInfo
    $entry.FileName = $wsl
    $entry.Arguments = "--distribution $distro --user root --cd / --exec $Command"
    $entry.UseShellExecute = $false
    $entry.RedirectStandardInput = $true
    $entry.RedirectStandardOutput = $true
    $entry.RedirectStandardError = $true
    $entry.StandardOutputEncoding = $utf8
    $proc = [Diagnostics.Process]::Start($entry)
    try {
        # Drain WSL launcher errors privately. They may contain host environment data.
        $errors = $proc.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        $inputFailed = $false
        try {
            $write = $proc.StandardInput.BaseStream.WriteAsync($Bytes, 0, $Bytes.Length)
            [void]$write.GetAwaiter().GetResult()
        }
        catch { $inputFailed = $true }
        finally { $proc.StandardInput.BaseStream.Close() }
        while ($null -ne ($line = $proc.StandardOutput.ReadLine())) {
            if ($Diagnostics -and $line -cmatch '^(stage=|failure stage=|stderr: )') { Write-ProvisionLog $line }
        }
        $proc.WaitForExit()
        [void]$errors.GetAwaiter().GetResult()
        if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne $ExpectedExit) {
            $message = "WSL command $Command exited $($proc.ExitCode)"
            Write-ProvisionLog $message
            throw "$message. See $logPath"
        }
        if ($inputFailed) { throw "Incomplete input to $Command. See $logPath" }
        if ($ExpectedExit -ge 0) { return $proc.ExitCode }
    } finally {
        $proc.Dispose()
    }
}

function Invoke-GuestScript([string]$Stage, [string]$Script, [int]$ExpectedExit = -1) {
    Write-ProvisionLog "stage=$Stage"
    # fd 3 carries trusted stage/error records. Discard normal stdout. Filter
    # arbitrary stderr before it crosses WSL: an apt URL or compiler message
    # can contain secrets even if the installer itself supplied none.
    $prologue = @'
set -Eeuo pipefail
exec 3>&1 >/dev/null
export LC_ALL=C
provision_stage() { SF_STAGE=$1; export SF_STAGE; printf 'stage=%s\n' "$1" >&3; }
provision_error() {
    printf 'failure stage=%s exit=%s file=%s line=%s command=%s\n' "$SF_STAGE" "$1" "$2" "$3" "${4//$'\n'/ }" >&3
}
export -f provision_stage provision_error
trap 'provision_error "$?" "${BASH_SOURCE[0]:-bootstrap}" "$LINENO" "$BASH_COMMAND"' ERR
exec 2> >(awk '
  BEGIN {
    packages = "(ca-certificates|git|curl|python3|build-essential|expect|bubblewrap|util-linux|time|gh)"
    pattern = "permission denied|operation not permitted|read-only file system|no space left on device|no such file or directory"
    pattern = pattern "|unable to locate package( " packages ")?|could not get lock|unable to acquire|dpkg was interrupted|unmet dependencies|hash sum mismatch"
    pattern = pattern "|temporary failure resolving|could not resolve host|unable to access|connection timed out|connection refused|certificate verification failed"
    pattern = pattern "|could not read username|authentication failed|repository not found|not a git repository|dubious ownership|reference is not a tree|couldn.t find remote ref|early eof|index-pack failed"
    pattern = pattern "|no rule to make target|fatal error|compilation terminated|undefined reference|out of memory|killed signal|checksums? did not match|unrecognized command-line option|cannot find|makefile:[0-9]+|collect2: error"
    pattern = pattern "|returned an error code [(][0-9]+[)]|error [0-9]+|exit status [0-9]+|no installation candidate|unable to fetch|cannot lock ref"
    pattern = pattern "|control process exited with error code|failed to start|failed to enable|bad unit file setting|failed to connect to bus|command not found|syntax error|error: (401|403|404|407|429|500|502|503)"
  }
  {
    rest = tolower($0)
    if (!match(rest, pattern) && !omitted++) { print "stderr: unclassified output withheld"; fflush() }
    while (match(rest, pattern)) {
      message = substr(rest, RSTART, RLENGTH)
      rest = substr(rest, RSTART + RLENGTH)
      if (!seen[message]++) { print "stderr: " message; fflush() }
    }
  }' >&3)
'@
    # A child (especially apt) must never consume the rest of bash's script from stdin.
    $wrapped = $prologue + "`nprovision_stage '$Stage'`n{" + "`n" + $Script + "`n} </dev/null`n"
    Invoke-GuestBytes '/bin/bash -se' ($utf8.GetBytes($wrapped.Replace("`r`n", "`n"))) -Diagnostics -ExpectedExit $ExpectedExit
}

function Start-Keeper {
    $actual = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = [IO.File]::ReadAllText((Join-Path $root 'account.sid')).Trim()
    if ($actual.User.Value -ne $sid -or $actual.Groups.Value -contains $admins.Value) {
        throw 'The keeper requires its dedicated non-administrator account.'
    }
    if ([IO.File]::ReadAllText($pinFile).Trim() -cne $ControlSha) { throw 'Control pin mismatch.' }
    if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -ge 1MB) {
        Move-Item -Force -LiteralPath $logPath -Destination ($logPath + '.previous')
    }
    Write-ProvisionLog "keeper start control=$ControlSha"
    $profileDir = [Environment]::GetFolderPath('UserProfile')
    $localData = [Environment]::GetFolderPath('LocalApplicationData')
    if (-not $profileDir -or -not $localData) { throw 'The dedicated Windows user profile must be loaded.' }
    $data = Join-Path $localData 'StockfishResearchWorker'
    $vm = Join-Path $data 'Ubuntu'
    [void][IO.Directory]::CreateDirectory($data)
    $registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    $registrations = @()
    if (Test-Path $registry) {
        $registrations = @(Get-ChildItem $registry | ForEach-Object { Get-ItemProperty $_.PSPath })
    }
    if (@($registrations | Where-Object { $_.DistributionName -ne $distro }).Count -ne 0) {
        throw 'Use an account dedicated exclusively to this worker distro.'
    }
    $settings = "[wsl2]`nprocessors=8`nmemory=12GB`nswap=0`nlocalhostForwarding=false`nguiApplications=false`n"
    $cfg = Join-Path $profileDir '.wslconfig'
    if ((Test-Path -LiteralPath $cfg) -and [IO.File]::ReadAllText($cfg) -ne $settings) {
        throw 'Existing .wslconfig differs from the dedicated worker settings.'
    }
    if (-not (Test-Path -LiteralPath $cfg)) { [IO.File]::WriteAllText($cfg, $settings, $utf8) }
    if ($registrations.Count -eq 0) {
        if ($Resume) { throw 'Resume requires the original registered WSL distribution.' }
        # Microsoft WSL DistributionInfo.json, commit cb19ae16acb8a02dbae8802ab55cc681015715c2.
        $url = 'https://releases.ubuntu.com/24.04.5/ubuntu-24.04.5-wsl-amd64.wsl'
        $digest = 'bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e'
        $image = Join-Path $data 'ubuntu-24.04.5.tar.gz'
        if ((-not (Test-Path -LiteralPath $image)) -or (Get-FileHash $image -Algorithm SHA256).Hash -ne $digest) {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $image
        }
        if ((Get-FileHash $image -Algorithm SHA256).Hash -ne $digest) { throw 'Ubuntu rootfs SHA-256 mismatch.' }
        & $wsl --import $distro $vm $image --version 2
        if ($LASTEXITCODE -ne 0) { throw 'WSL2 import failed.' }
        Remove-Item -LiteralPath $image
    } else {
        $registered = $registrations[0]
        $base = $registered.BasePath -replace '^\\\\\?\\', ''
        if ($registered.Version -ne 2 -or [IO.Path]::GetFullPath($base) -ne [IO.Path]::GetFullPath($vm)) {
            throw 'Existing distro version or VHD location differs from the dedicated installation.'
        }
    }

    $configured = Join-Path $data 'wsl-configured-v1'
    if (-not $Resume -and -not (Test-Path -LiteralPath $configured)) {
        Invoke-GuestScript 'WSL configuration' @'
umask 022
cat > /etc/wsl.conf <<'WSLCONF'
[boot]
systemd=true
[automount]
enabled=false
mountFsTab=false
[interop]
enabled=false
appendWindowsPath=false
WSLCONF
sync
'@
    }

    # Record the configuration generation across a cold start. WSL2 reads the
    # distro policy at init; another distro can keep the shared VM alive.
    $checkStamp = @'
set -eu
test -f /etc/wsl.conf
test ! -L /etc/wsl.conf
generation=$(TZ=UTC0 stat -c '%i %z' /etc/wsl.conf && sha256sum /etc/wsl.conf)
stamp=/etc/stockfish-wsl-isolation/applied
if [[ ! -s "$stamp" ]] || ! cmp -s "$stamp" <(printf '%s\n' "$generation"); then
    exit 42
fi
'@
    if ((Invoke-GuestScript 'check last applied distro policy' $checkStamp -ExpectedExit 42) -eq 42) {
        Invoke-GuestScript 'prepare dedicated distro isolation verification' @'
set -eu
if [[ $(cat /proc/1/comm) == systemd ]]; then
    # A missing unit is normal on an interrupted first install. Never stop a live campaign.
    units=$(systemctl list-units --all --type=service --no-legend --plain stockfish-worker.service)
    if [[ -n "$units" ]]; then
        state=$(systemctl show -p ActiveState --value stockfish-worker.service)
        [[ "$state" == inactive || "$state" == failed ]]
    fi
else
    test ! -e /etc/systemd/system/stockfish-worker.service
fi
test -f /etc/wsl.conf
test ! -L /etc/wsl.conf
install -d -m 0755 -o root -g root /etc/stockfish-wsl-isolation
cd /etc/stockfish-wsl-isolation
umask 022
[[ $(stat -c %u /etc/wsl.conf) == 0 ]]
(( (8#$(stat -c %a /etc/wsl.conf) & 022) == 0 ))
cat /proc/sys/kernel/random/boot_id > previous-instance
readlink /proc/1/ns/pid >> previous-instance
TZ=UTC0 stat -c '%i %z' /etc/wsl.conf > pending
sha256sum /etc/wsl.conf >> pending
sync -f pending
'@
        & $wsl --terminate $distro
        if ($LASTEXITCODE -ne 0) { throw 'Could not cold-start the dedicated worker distro.' }
        Invoke-GuestScript 'confirm fresh dedicated distro boot' @'
set -eu
cd /etc/stockfish-wsl-isolation
[[ $(cat /proc/1/comm) == systemd ]]
previous=$(cat previous-instance)
current=$(cat /proc/sys/kernel/random/boot_id && readlink /proc/1/ns/pid)
[[ -n "$previous" && "$previous" != "$current" ]]
generation=$(TZ=UTC0 stat -c '%i %z' /etc/wsl.conf && sha256sum /etc/wsl.conf)
cmp -s pending <(printf '%s\n' "$generation")
[[ ! -v WSL_INTEROP ]]
chmod 0644 pending
mv -fT pending applied
'@
        [IO.File]::WriteAllText($configured, '1', $utf8)
    }

    $serviceAction = 'start'
    $ready = Join-Path $data ('installed-' + $ControlSha)
    if (-not (Test-Path -LiteralPath $ready) -or [IO.File]::ReadAllText($ready) -cne $ControlSha) {
        $install = @'
set -eu
export DEBIAN_FRONTEND=noninteractive GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
provision_stage "bootstrap package installation"
dpkg --configure --pending
apt-get update
apt-get install -y --no-install-recommends git ca-certificates
provision_stage "control Git checkout"
tmp=$(mktemp -d /var/tmp/stockfish-control.XXXXXXXX)
trap 'rm -rf -- "$tmp"' EXIT
git init -q "$tmp"
git -C "$tmp" remote add origin https://github.com/ShadyF4RR/Stockfish.git
git -C "$tmp" fetch -q --depth=1 --no-tags origin APPROVED_SHA
git -C "$tmp" checkout -q --detach APPROVED_SHA
test "$(git -C "$tmp" rev-parse HEAD)" = APPROVED_SHA
provision_stage "worker installation"
/bin/bash "$tmp/research-lab/dedicated-worker/install-guest.sh" APPROVED_SHA
'@
        Invoke-GuestScript 'guest provisioning' ($install.Replace('APPROVED_SHA', $ControlSha))
        [IO.File]::WriteAllText($ready, $ControlSha, $utf8)
        $serviceAction = 'restart'
    }
    Invoke-GuestScript 'verify live dedicated distro isolation' @'
bash /opt/stockfish-lab/research-lab/dedicated-worker/check-wsl-isolation.sh
provision_stage "verified disabled distro interop and Windows drive automount"
'@
    if (Test-Path -LiteralPath $tokenFile) {
        Write-ProvisionLog "stage=credential import"
        $bytes = Read-Envelope $tokenFile
        try { Invoke-GuestBytes '/usr/local/libexec/stockfish-worker-set-credential' $bytes }
        finally { [Array]::Clear($bytes, 0, $bytes.Length) }
        Remove-Item -LiteralPath $tokenFile
        $serviceAction = 'restart'
    }
    $launch = @'
set -eu
test "$(cat /etc/stockfish-worker/control.sha)" = APPROVED_SHA
test "$(git -C /opt/stockfish-lab rev-parse HEAD)" = APPROVED_SHA
test -s /etc/stockfish-worker/github-token
systemctl enable stockfish-worker.service
systemctl SERVICE_ACTION stockfish-worker.service
sleep 2
systemctl is-active --quiet stockfish-worker.service
'@
    Invoke-GuestScript 'service readiness' ($launch.Replace('APPROVED_SHA', $ControlSha).Replace('SERVICE_ACTION', $serviceAction))
    $ownerId = (& $wsl --distribution $distro --user sfworker --exec /usr/bin/python3 -I /opt/stockfish-lab/research-lab/dedicated-worker/worker.py identity --state /var/lib/stockfish-worker).Trim()
    if ($LASTEXITCODE -ne 0 -or $ownerId -cnotmatch '^pc-[a-f0-9]{32}$') { throw 'No valid PC worker identity.' }
    $initialized = @{ owner = $ownerId; control_sha = $ControlSha; started_at = [DateTime]::UtcNow.ToString('o'); distro = $distro }
    [IO.File]::WriteAllText((Join-Path $handoff 'last-start.json'), ($initialized | ConvertTo-Json), $utf8)
    # Systemd units alone do not keep a WSL instance alive. Task Scheduler owns this foreground process.
    & $wsl --distribution $distro --user sfworker --exec /usr/bin/sleep infinity
    throw 'The WSL keeper exited; Task Scheduler will retry.'
}

if ($Keeper) {
    try { Start-Keeper } catch {
        $failure = @{ time = [DateTime]::UtcNow.ToString('o'); error = $_.Exception.Message }
        [IO.File]::WriteAllText((Join-Path $handoff 'last-error.json'), ($failure | ConvertTo-Json), $utf8)
        exit 1
    }
    exit 0
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole($admins) -or -not [Environment]::Is64BitProcess -or $PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Run from elevated 64-bit Windows PowerShell 5.1.'
}
if ([Environment]::OSVersion.Version.Build -lt 22000 -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'This installer supports Windows 11 x64 with WSL2 hardware virtualization.'
}
$missing = @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform') | Where-Object {
    (Get-WindowsOptionalFeature -Online -FeatureName $_).State -ne 'Enabled'
}
if ($missing) {
    & $wsl --install --no-distribution
    if ($LASTEXITCODE -notin @(0,3010)) { throw 'WSL installation failed. Enable virtualization and install current WSL2.' }
    Write-Output 'WSL platform installation started. Reboot Windows, then run this installer again.'
    exit 3010
}
& $wsl --version
if ($LASTEXITCODE -ne 0) { throw 'Install or update Microsoft WSL, then rerun. A systemd-capable WSL2 runtime is required.' }

$account = Get-LocalUser -Name $winAccount -ErrorAction SilentlyContinue
$record = Join-Path $root 'account.sid'
if ($account -and ((-not (Test-Path -LiteralPath $record)) -or [IO.File]::ReadAllText($record).Trim() -ne $account.SID.Value)) {
    throw 'The dedicated account name is already in use. Refusing to adopt that account.'
}
$task = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($task -and (-not $account -or $task.Principal.UserId -notin @($winAccount, "$env:COMPUTERNAME\$winAccount", $account.SID.Value))) {
    throw 'The task name belongs to a different principal.'
}
if ($Resume -and (-not $account -or -not $task)) { throw 'Resume requires the existing worker account and scheduled task.' }
if ($task -and $task.State -eq 'Running') {
    throw 'The keeper is already running. Drain work and stop it before changing the pin or rotating a token.'
}

$oldRegistration = $null
$restoreEnabled = $false
if ($Resume) {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $oldRegistration = $scheduler.GetFolder('\').GetTask($taskName)
    $restoreEnabled = $oldRegistration.Enabled
    $oldRegistration.Enabled = $false
}
try {
    if ($Resume -and $oldRegistration.GetInstances(0).Count -gt 0) {
        throw 'The previous keeper is still running. Wait for provisioning to exit before resuming.'
    }
    Write-PrivateDirectory $root $null $null
    Write-PrivateDirectory $private $null $null
    $passwordFile = Join-Path $private 'host-account.dpapi'
    if (-not $account) {
        $rnd = New-Object byte[] 48
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($rnd) } finally { $rng.Dispose() }
        $password = 'Sf!9' + [Convert]::ToBase64String($rnd)
        $pwBytes = $utf8.GetBytes($password)
        try { Write-Envelope $passwordFile $pwBytes } finally { [Array]::Clear($pwBytes, 0, $pwBytes.Length) }
        $account = New-LocalUser -Name $winAccount -Password (ConvertTo-SecureString $password -AsPlainText -Force) -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword
        [IO.File]::WriteAllText($record, $account.SID.Value, $utf8)
    } else {
        $pwBytes = Read-Envelope $passwordFile
        try { $password = $utf8.GetString($pwBytes) } finally { [Array]::Clear($pwBytes, 0, $pwBytes.Length) }
    }
    if (@(Get-LocalGroupMember -SID $admins | Where-Object { $_.SID -eq $account.SID }).Count) {
        throw 'Remove the dedicated account from Administrators before installation.'
    }
    Write-PrivateDirectory $root $account.SID 'ReadAndExecute'
    Write-PrivateDirectory $handoff $account.SID 'Modify'
    Write-PrivateDirectory $inbox $account.SID 'ReadAndExecute'
    $entry = Join-Path $root ('Install-StockfishWorker-' + $ControlSha + '.ps1')
    if ([IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($entry)) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $entry -Force
    }
    # Resume reuses the pending DPAPI envelope or the credential already in WSL.
    if (-not $Resume -or $null -ne $WorkerToken) {
        if ($null -eq $WorkerToken) {
            $WorkerToken = Read-Host 'Scoped STOCKFISH_WORKER_TOKEN (hidden)' -AsSecureString
        }
        $tokenBytes = ConvertTo-SecretBytes $WorkerToken
        try {
            if ($tokenBytes.Length -lt 20 -or $tokenBytes.Length -gt 4096) { throw 'Invalid worker credential length.' }
            Write-Envelope $tokenFile $tokenBytes
            # The service can delete this envelope after import but cannot create a replacement
            # link in the administrator-owned input directory.
            $fileAcl = [IO.File]::GetAccessControl($tokenFile)
            $fileAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($account.SID,'Delete','Allow')))
            [IO.File]::SetAccessControl($tokenFile, $fileAcl)
        } finally { [Array]::Clear($tokenBytes, 0, $tokenBytes.Length) }
    }

    $exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $entry + '" -Keeper -ControlSha ' + $ControlSha
    if ($Resume) { $arguments += ' -Resume' }
    $action = New-ScheduledTaskAction -Execute $exe -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    try {
        Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -User "$env:COMPUTERNAME\$winAccount" -Password $password -Force | Out-Null
    } finally { $password = $null }
    [IO.File]::WriteAllText($pinFile, $ControlSha, $utf8)
    if ($Resume) { Enable-ScheduledTask -TaskName $taskName -TaskPath '\' | Out-Null }
    $restoreEnabled = $false
} finally {
    if ($restoreEnabled) { Enable-ScheduledTask -TaskName $taskName -TaskPath '\' | Out-Null }
}
Start-ScheduledTask -TaskName $taskName -TaskPath '\'
Write-Output "Scheduled $taskName. Verify guest startup in $handoff\last-start.json and then test a Windows reboot."
