# Register the Intel Windows 11 testing PC

The PC runs a pull worker under a dedicated, unprivileged Windows account and a WSL2 systemd service. It accepts a fixed chess manifest from the fork. It has no general GitHub Actions shell, OpenAI credentials or inbound listener. GitHub Actions runs the four-hour dispatcher on GitHub. The Windows startup task starts the worker after a reboot.

## GitHub setup

1. In the fork's **Settings → Rules → Rulesets**, protect `master`, `research/baseline`, `research/integration`, experiment branches and the control branches against direct updates from the worker credential. Require pull requests on those branches, block force pushes and deletion, and give the worker no bypass. Protect `research/testing-queue` and `research/testing-evidence/**` against force push and deletion while allowing ordinary writes by the trusted dispatcher and worker. Do not grant untrusted branch writers access to these refs. The contents permission itself is repository-wide.
2. Create an expiring fine-grained GitHub token under **Settings → Developer settings → Personal access tokens → Fine-grained tokens**. Select resource owner **ShadyF4RR**, **Only select repositories → Stockfish**, and **Repository permissions → Contents: Read and write**. Metadata read access is automatic. Leave Actions, Workflows and Administration unselected. Apply the branch protections to the token owner's role, including administrators. Enter this credential only at the installer's hidden prompt on the PC.
3. The fork's Actions workflow must be enabled and permitted to write the two data queues. The checkout uses a commit-pinned GitHub action. The scheduler accepts manifests only from the fork's default branch. New approved manifests go in `research-lab/approved-matches/` via reviewed control changes.

## Windows installation

Enable CPU virtualization in firmware. Use a network connection that permits GitHub, Microsoft WSL, Ubuntu packages and the official Stockfish network download. Open **64-bit Windows PowerShell 5.1 as Administrator**. Use the full worker commit and installer SHA-256 from the deployment report.

```powershell
$pin = Read-Host 'Full worker commit from the deployment report'
if ($pin -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid commit' }
$url = "https://raw.githubusercontent.com/ShadyF4RR/Stockfish/$pin/research-lab/dedicated-worker/Install-StockfishWorker.ps1"
$file = Join-Path $env:TEMP 'Install-StockfishWorker.ps1'
Invoke-WebRequest -UseBasicParsing $url -OutFile $file
if ((Get-FileHash $file -Algorithm SHA256).Hash -ne 'e4019a8113cf3d98567d46c024f6de1c332cb4b0c726139aedaddc931d04fb62') { throw 'Installer checksum mismatch' }
Unblock-File -LiteralPath $file
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File $file -ControlSha $pin
```

If the installer enables WSL and requests a reboot, reboot and repeat this block. It imports the hash-pinned Ubuntu image, builds pinned Fastchess and installs the service. Allow provisioning to finish. The guest can use up to eight vCPUs and 12 GiB, with a 10 GiB hard memory limit on the worker service.

```powershell
Get-ScheduledTask -TaskName 'Stockfish Research Worker' | Select-Object State
$handoff = "$env:ProgramData\StockfishResearchWorker\Handoff"
Get-Content "$handoff\last-start.json" | ConvertFrom-Json | Select-Object owner,control_sha,started_at
```

If startup fails, inspect `last-error.json` in that directory. Match the control SHA and copy only the public `pc-…` owner string. In the fork's **Settings → Secrets and variables → Actions → Variables**, create **STOCKFISH_PC_OWNER** with that value. The next tick enrolls the PC. The service reports readiness after its startup, capacity and Fastchess checks. A subsequent tick releases the pending request. The service then claims the global slot. Leave the machine powered and awake on AC for testing. After an idle reboot, verify the same owner and a new startup timestamp.

## Resume a failed Linux provisioning attempt

Use the download and checksum commands above with the new pin. For an existing installation, use this final command in elevated 64-bit Windows PowerShell 5.1.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File $file -ControlSha $pin -Resume
```

Resume requires the original task, Windows account SID and registered WSL distribution. Run it after the failed keeper exits. The installer temporarily disables new launches of that task while updating its definition. It reuses the pending encrypted token or the credential already in the guest. Completed releases and Fastchess builds are checked before reuse. The worker's game and identity directories stay in the existing VHD.

On the first resume with the isolation fix, the keeper records the existing `/etc/wsl.conf` configuration and cold-starts only `StockfishWorker-2404`. It requires a different distro instance with systemd as PID 1 and the identical root-owned configuration. It refuses to interrupt an active worker. Later starts reuse the verification while the configuration generation matches.

WSL 3.0.5 registers the Windows executable handler for the WSL2 VM. A systemd distribution can also have a local interop socket even with Windows process creation disabled. The keeper checks explicit `false` values for `[interop] enabled`, `appendWindowsPath`, `[automount] enabled` and `mountFsTab`. It checks a direct WSL session for an active interop channel and inspects actual mounts for Windows drive shares, including 9p and virtiofs. The service repeats the checks at startup and denies access to the interop socket directory and AF_VSOCK. Other distributions retain their own settings. [WSL 3.0.5 interop design](https://github.com/microsoft/WSL/blob/e7ef9a88db1e5e5745ed93bdce3eb9d8ee5f8aee/doc/docs/technical-documentation/interop.md) · [Distro channel setup](https://github.com/microsoft/WSL/blob/e7ef9a88db1e5e5745ed93bdce3eb9d8ee5f8aee/src/linux/init/config.cpp#L939)

Look for `stage=verified disabled distro interop and Windows drive automount` in the current attempt. An isolation helper exit of **21** means missing policy or boot receipt, **22** means ambiguous or unsafe settings, **23** means changed configuration, **24** means a session advertises Windows interop, and **25** means a Windows drive mount or unreadable mount evidence.

Read the protected diagnostics in elevated PowerShell. Each attempt starts with its control SHA. A Linux failure records its stage, script, line, exit code and Bash command as written. Shell variables in those commands retain their source names. Recognized stderr fragments are recorded, while other subprocess output is withheld to protect credentials. The log rotates on keeper startup at 1 MiB, retaining one previous copy.

```powershell
$handoff = "$env:ProgramData\StockfishResearchWorker\Handoff"
Get-Content "$handoff\provisioning.log" -Tail 40
```

A successful installer launch only registers the task. Check for a fresh `last-start.json` with the requested SHA after the service readiness checks. Enrollment and completion of a real match are recorded separately.

## Read the evidence

- The [dispatcher](https://github.com/ShadyF4RR/Stockfish/actions/workflows/experiment-scheduler.yml) runs at minute 57 of 02, 06, 10, 14, 18 and 22 UTC. Check the event field for a genuine scheduled run.
- The [testing queue](https://github.com/ShadyF4RR/Stockfish/blob/research/testing-queue/queue.json) stores delivery, claim, start, attempt and terminal result. A recent readiness heartbeat allows dispatch. The first verified real match sets PC preference. When another approved request exists, the next eligible tick releases it after settlement.
- Results land on `research/testing-evidence/<experiment>/<lease>`. Inspect the PGN, paired W/D/L, Elo interval, benchmark and receipt. Candidate processes lack network access and cannot read the controller's token. A returned PC reuses the same lease, disjoint opening batches and durable per-game PGNs. A missing or corrupt PGN for an already admitted game closes the attempt as invalid and preserves the evidence. This prevents silently replaying that game. A missing local lease identity requires reconciliation.
- To pause releases use the trusted dispatcher on the protected control revision, `dispatch.py pause --reason "maintenance"`. Drain an active game before servicing the PC. Preserve the WSL VHD and worker state. Resume with `dispatch.py resume`.
