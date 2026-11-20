#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ControlSha,
    [Security.SecureString]$WorkerToken,
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

function Invoke-GuestBytes([string]$Command, [byte[]]$Bytes) {
    # Commands are fixed below. Credential bytes pass only through the child's stdin.
    $entry = New-Object Diagnostics.ProcessStartInfo
    $entry.FileName = $wsl
    $entry.Arguments = "--distribution $distro --user root --exec $Command"
    $entry.UseShellExecute = $false
    $entry.RedirectStandardInput = $true
    $proc = [Diagnostics.Process]::Start($entry)
    try {
        $proc.StandardInput.BaseStream.Write($Bytes, 0, $Bytes.Length)
        $proc.StandardInput.Close()
        $proc.WaitForExit()
        if ($proc.ExitCode -ne 0) { throw "Guest provisioning command exited $($proc.ExitCode)" }
    } finally {
        $proc.Dispose()
    }
}

function Invoke-GuestScript([string]$Script) {
    Invoke-GuestBytes '/bin/bash -se' ($utf8.GetBytes($Script.Replace("`r`n", "`n")))
}

function Start-Keeper {
    $actual = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = [IO.File]::ReadAllText((Join-Path $root 'account.sid')).Trim()
    if ($actual.User.Value -ne $sid -or $actual.Groups.Value -contains $admins.Value) {
        throw 'The keeper requires its dedicated non-administrator account.'
    }
    if ([IO.File]::ReadAllText($pinFile).Trim() -cne $ControlSha) { throw 'Control pin mismatch.' }
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
    [IO.File]::WriteAllText($cfg, $settings, $utf8)
    if ($registrations.Count -eq 0) {
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
    if (-not (Test-Path -LiteralPath $configured)) {
        Invoke-GuestScript @'
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
        & $wsl --terminate $distro
        if ($LASTEXITCODE -ne 0) { throw 'Cannot restart the dedicated distro with isolation enabled.' }
        [IO.File]::WriteAllText($configured, '1', $utf8)
    }

    $serviceAction = 'start'
    $ready = Join-Path $data ('installed-' + $ControlSha)
    if (-not (Test-Path -LiteralPath $ready)) {
        $install = @'
set -eu
export DEBIAN_FRONTEND=noninteractive GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
apt-get update
apt-get install -y --no-install-recommends git ca-certificates
tmp=$(mktemp -d /var/tmp/stockfish-control.XXXXXXXX)
trap 'rm -rf -- "$tmp"' EXIT
git init -q "$tmp"
git -C "$tmp" remote add origin https://github.com/ShadyF4RR/Stockfish.git
git -C "$tmp" fetch -q --depth=1 --no-tags origin APPROVED_SHA
git -C "$tmp" checkout -q --detach APPROVED_SHA
test "$(git -C "$tmp" rev-parse HEAD)" = APPROVED_SHA
/bin/bash "$tmp/research-lab/dedicated-worker/install-guest.sh" APPROVED_SHA
'@
        Invoke-GuestScript ($install.Replace('APPROVED_SHA', $ControlSha))
        [IO.File]::WriteAllText($ready, $ControlSha, $utf8)
        $serviceAction = 'restart'
    }
    if (Test-Path -LiteralPath $tokenFile) {
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
    Invoke-GuestScript ($launch.Replace('APPROVED_SHA', $ControlSha).Replace('SERVICE_ACTION', $serviceAction))
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
if ($task -and $task.State -eq 'Running') {
    throw 'The keeper is already running. Drain work and stop it before changing the pin or rotating a token.'
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
$entry = Join-Path $root 'Install-StockfishWorker.ps1'
if ([IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($entry)) {
    Copy-Item -LiteralPath $PSCommandPath -Destination $entry -Force
}
[IO.File]::WriteAllText($pinFile, $ControlSha, $utf8)
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
$exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $entry + '" -Keeper -ControlSha ' + $ControlSha
$action = New-ScheduledTaskAction -Execute $exe -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
try {
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -User "$env:COMPUTERNAME\$winAccount" -Password $password -Force | Out-Null
} finally { $password = $null }
Start-ScheduledTask -TaskName $taskName -TaskPath '\'
Write-Output "Scheduled $taskName. Verify guest startup in $handoff\last-start.json and then test a Windows reboot."
