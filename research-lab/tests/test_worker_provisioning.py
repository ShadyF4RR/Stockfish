"""Exercise the installer's actual Bash diagnostics with controlled failing tools."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

WORKER = Path(__file__).resolve().parents[1] / "dedicated-worker"
INSTALLER = (WORKER / "Install-StockfishWorker.ps1").read_text()


def embedded(name):
    return re.search(rf"\${name} = @'\n(.*?)\n'@", INSTALLER, re.S)[1]


class ProvisioningDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.secret = "github_pat_SyntheticTestSecret123"
        self.env = dict(os.environ, SF_PRIVATE_VALUE=self.secret,
                        PATH=f"{self.work}:{os.environ['PATH']}")
        self.stage = "bootstrap package installation"
        self.script = embedded("install").replace("APPROVED_SHA", "a" * 40)

    def stub(self, name, body):
        path = self.work / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def run_guest(self, script):
        wrapper = embedded("prologue") + "\nprovision_stage 'bootstrap'\n{\n" + script + "\n} </dev/null\n"
        return subprocess.run(["bash", "-se"], input=wrapper, text=True, env=self.env, cwd=self.work,
                              capture_output=True, timeout=10)

    def test_packages_report_exact_failure_and_allowlisted_stderr(self):
        self.stub("dpkg","exit 0")
        self.stub("apt-get", 'echo "$SF_PRIVATE_VALUE"; echo "Permission denied: $SF_PRIVATE_VALUE" >&2; exit 100')
        result = self.run_guest(self.script)
        self.assertEqual(result.returncode, 100)
        self.assertIn("stage=" + self.stage, result.stdout)
        self.assertRegex(result.stdout, r"exit=100 file=bootstrap line=\d+ command=apt-get update")
        self.assertIn("stderr: permission denied", result.stdout)
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_checkout_has_its_own_stage(self):
        self.stub("dpkg","exit 0")
        self.stub("apt-get","cat >/dev/null")
        self.stub("git",'if [[ $* == *fetch* ]]; then echo "authentication failed: $SF_PRIVATE_VALUE" >&2; exit 128; fi')
        result = self.run_guest(self.script)
        self.assertEqual(result.returncode, 128)
        self.assertRegex(result.stdout, r'failure stage=control Git checkout exit=128 .*command=git -C "\$tmp" fetch')
        self.assertIn("stderr: authentication failed", result.stdout)
        self.assertNotIn(self.secret,result.stdout + result.stderr)

    def test_guest_script_preserves_child_stage_and_line(self):
        # Exercise the exact child hook and actual public compilation command without installing packages.
        guest = (WORKER / "install-guest.sh").read_text()
        prefix = guest[:guest.index('umask 022')]
        fragment = guest[guest.index('    provision_stage "Fastchess compilation"'):guest.index('    chown -R root:root')]
        child = self.work / "guest.sh"
        child.write_text(prefix + 'tmp=$PWD\n' + fragment)
        self.stub("runuser",'echo "fatal error: $SF_PRIVATE_VALUE" >&2; exit 2')
        result = self.run_guest('bash ./guest.sh')
        self.assertEqual(result.returncode,2)
        self.assertRegex(result.stdout,r'failure stage=Fastchess compilation exit=2 file=./guest.sh line=\d+ command=runuser -u nobody -- make')
        self.assertIn("stderr: fatal error",result.stdout)
        self.assertNotIn(self.secret,result.stdout + result.stderr)

    def test_resume_after_service_failure_reuses_verified_fastchess(self):
        # Run the real checkout/build/verification block against a small local Git fixture.
        repo = self.work / "source"
        repo.mkdir()
        (repo / "app/tests/data").mkdir(parents=True)
        (repo / "app/tests/data/openings.epd").write_text("fixture opening\n")
        (repo / "Makefile").write_text("all:\n\ttouch fastchess\n\tchmod +x fastchess\n")
        git = ["git", "-C", str(repo)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["-c","user.name=Fixture","-c","user.email=test@example.org",
                              "commit","-qm","Fixture"],check=True)
        pin = subprocess.check_output(git + ["rev-parse","HEAD"],text=True).strip()
        self.stub("runuser",'shift 3; exec "$@"')
        self.stub("chown","exit 0")
        guest = (WORKER / "install-guest.sh").read_text()
        a = guest.index('provision_stage "Fastchess Git checkout"')
        b = guest.index('provision_stage "service configuration"')
        fragment = guest[a:b].replace("/opt/stockfish-fastchess", str(self.work / "installed"))
        script = ('tmp=$(mktemp -d); fc_repo=' + repo.as_uri() + '; fc_pin=' + pin +
                  '\nbook_sha=$(sha256sum source/app/tests/data/openings.epd | cut -d" " -f1)\n' + fragment)
        first = self.run_guest(script + 'provision_stage "service configuration"\nfalse')
        self.assertEqual(first.returncode,1)
        self.assertIn("stage=Fastchess compilation",first.stdout)
        self.assertIn("failure stage=service configuration",first.stdout)
        binary = self.work / "installed/fastchess"
        inode = binary.stat().st_ino
        second = self.run_guest(script)
        self.assertEqual(second.returncode,0,second.stdout)
        self.assertIn("stage=Fastchess verification",second.stdout)
        self.assertNotIn("stage=Fastchess compilation",second.stdout)
        self.assertEqual(inode,binary.stat().st_ino)

    @unittest.skipUnless(shutil.which("pwsh"), "requires PowerShell and a Bash test shim")
    def test_powershell_process_bridge_keeps_credential_channel_private(self):
        self.stub("wsl", '''while [[ $1 != --exec ]]; do shift; done
shift
if [[ $1 == /fixture-import ]]; then cat | tee /dev/stderr; exit 7; fi
exec "$@"''')
        driver = self.work / "test.ps1"
        driver.write_text("""param($Installer,$Shim,$Log)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
$wsl = $Shim
$distro = 'fixture'
$logPath = $Log
$ast = [Management.Automation.Language.Parser]::ParseFile($Installer,[ref]$null,[ref]$null)
foreach ($name in 'Write-ProvisionLog','Invoke-GuestBytes','Invoke-GuestScript') {
    $node = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false)
    . ([scriptblock]::Create($node.Extent.Text))
}
if ((Invoke-GuestScript 'stamp fixture' 'exit 42' -ExpectedExit 42) -ne 42) { throw 'Missing refresh signal' }
if ((Invoke-GuestScript 'stamp fixture' 'true' -ExpectedExit 42) -ne 0) { throw 'Missing valid stamp signal' }
try { Invoke-GuestScript 'fixture readiness' 'systemctl is-active --quiet stockfish-worker.service' }
catch { if ($_ -notmatch 'exited 3') { throw } }
try { Invoke-GuestBytes '/fixture-import' ($utf8.GetBytes($env:SF_PRIVATE_VALUE)) }
catch { if ($_ -notmatch 'exited 7') { throw } }
""")
        self.stub("systemctl", 'echo "Permission denied: $SF_PRIVATE_VALUE" >&2; exit 3')
        log = self.work / "log"
        result = subprocess.run(["pwsh","-NoProfile","-File",str(driver),str(WORKER / "Install-StockfishWorker.ps1"),
                                 str(self.work / "wsl"),str(log)],capture_output=True,text=True,env=self.env,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        data = log.read_text()
        self.assertIn("failure stage=fixture readiness exit=3",data)
        self.assertIn("command=systemctl is-active --quiet stockfish-worker.service",data)
        self.assertIn("stderr: permission denied",data)
        self.assertIn("WSL command /fixture-import exited 7",data)
        self.assertNotIn(self.secret,data + result.stderr + result.stdout)
