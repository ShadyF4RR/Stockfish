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
if ((Get-FileHash $file -Algorithm SHA256).Hash -ne '3afc21619a2f8cabe7cf9d44042407a4a4cf9f316aacc0c8c9992fe827cc9998') { throw 'Installer checksum mismatch' }
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

## Read the evidence

- The [dispatcher](https://github.com/ShadyF4RR/Stockfish/actions/workflows/experiment-scheduler.yml) runs at minute 57 of 02, 06, 10, 14, 18 and 22 UTC. Check the event field for a genuine scheduled run.
- The [testing queue](https://github.com/ShadyF4RR/Stockfish/blob/research/testing-queue/queue.json) stores delivery, claim, start, attempt and terminal result. A recent readiness heartbeat allows dispatch. The first verified real match sets PC preference. When another approved request exists, the next eligible tick releases it after settlement.
- Results land on `research/testing-evidence/<experiment>/<lease>`. Inspect the PGN, paired W/D/L, Elo interval, benchmark and receipt. Candidate processes lack network access and cannot read the controller's token. A returned PC reuses the same lease, disjoint opening batches and durable per-game PGNs. A missing or corrupt PGN for an already admitted game closes the attempt as invalid and preserves the evidence. This prevents silently replaying that game. A missing local lease identity requires reconciliation.
- To pause releases use the trusted dispatcher on the protected control revision, `dispatch.py pause --reason "maintenance"`. Drain an active game before servicing the PC. Preserve the WSL VHD and worker state. Resume with `dispatch.py resume`.
