"""Run the worker's real isolation checks against WSL2 guest fixtures."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_worker_provisioning import INSTALLER, WORKER, embedded


HELPER = WORKER / "check-wsl-isolation.sh"
POLICY = re.search(r"<<'WSLCONF'\n(.*?)\nWSLCONF", INSTALLER, re.S)[1] + "\n"
MOUNTS = ("32 31 8:0 / / rw - ext4 /dev/sda rw\n"
          "40 32 0:42 / /proc/sys/fs/binfmt_misc rw - binfmt_misc binfmt_misc rw\n"
          "41 32 0:43 / /usr/lib/wsl/drivers ro - 9p drivers ro,aname=drivers\n")


class WslIsolation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.conf = self.root / "wsl.conf"
        self.stamp = self.root / "applied"
        self.mounts = self.root / "mountinfo"
        self.mounts.write_text(MOUNTS)
        self.env = dict(os.environ, TZ="UTC0")
        self.env.pop("WSL_INTEROP", None)
        self.apply(POLICY)

    def apply(self, policy):
        self.conf.write_text(policy)
        data = subprocess.check_output(["stat", "-c", "%i %z", self.conf], env=self.env)
        self.stamp.write_bytes(data + subprocess.check_output(["sha256sum", self.conf]))

    def verify(self, expected, **env):
        result = subprocess.run(["bash", "-c", 'source "$1"; shift; verify_isolation "$@"',
                                 "fixture", HELPER, self.conf, self.stamp, self.mounts],
                                env=dict(self.env, **env), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, expected, result.stderr)

    def test_shared_enabled_handler_with_disabled_guest(self):
        # Substitute the fixture proc path in the actual provisioning preflight:
        # restoring the old existence test here must reproduce the original failure.
        marker = self.root / "WSLInterop"
        marker.write_text("enabled\ninterpreter /init\n")
        guest = (WORKER / "install-guest.sh").read_text()
        begin = guest.index('provision_stage "distro-local')
        end = guest.index("export GIT_CONFIG", begin)
        block = guest[begin:end].replace("/proc/sys/fs/binfmt_misc/WSLInterop", str(marker))
        (self.root / HELPER.name).write_text(HELPER.read_text()
            .replace("/etc/wsl.conf", str(self.conf))
            .replace("/etc/stockfish-wsl-isolation/applied", str(self.stamp))
            .replace("/proc/self/mountinfo", str(self.mounts)))
        script = self.root / "install-guest.sh"
        script.write_text('set -eu\nprovision_stage() { echo "$1"; }\n' + block)
        result = subprocess.run(["bash", script], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verified disabled distro interop", result.stdout)
        self.assertTrue(marker.read_text().startswith("enabled"))
        self.assertNotIn("/proc/sys/fs/binfmt_misc/WSLInterop", guest)

    def test_all_four_settings_must_be_explicit_and_unambiguous(self):
        for section in ("interop", "automount"):
            for key in ("enabled", "appendWindowsPath" if section == "interop" else "mountFsTab"):
                header, body = POLICY.split(f"[{section}]")
                current, boundary, rest = body.partition("[")
                for replacement in ("", f"{key}=true", f"{key}=false\n{key}=true"):
                    with self.subTest(section=section, key=key, replacement=replacement):
                        self.apply(header + f"[{section}]" + current.replace(f"{key}=false", replacement)
                                   + boundary + rest)
                        self.verify(22)
        self.apply(POLICY.replace("[interop]", "[network]"))
        self.verify(22)
        self.apply(POLICY.replace("[interop]", "root=elsewhere\\\n[interop]"))
        self.verify(22)

    def test_safe_comments_and_crlf(self):
        self.apply(POLICY.replace("enabled=false", "\tenabled = false # disabled").replace("\n", "\r\n"))
        self.verify(0)

    def test_missing_or_stale_boot_receipt(self):
        self.stamp.unlink()
        self.verify(21)
        self.apply(POLICY)
        self.conf.write_text(POLICY + "# later edit\n")
        self.verify(23)

    def test_live_interop_session_is_rejected(self):
        self.verify(24, WSL_INTEROP="/run/WSL/57_interop")
        self.verify(24, WSL_INTEROP="")

    def test_live_mount_evidence(self):
        for mounted in ("drvfs D: rw", "9p drvfs rw", "9p drvfsa ro", "9p D: rw", "9p C: rw,aname=drvfs",
                        "virtiofs drvfsa ro", "virtiofs drvfs rw", "virtiofs C: rw",
                        "virtiofs {11111111-2222-3333-4444-555555555555} ro"):
            with self.subTest(mounted=mounted):
                self.mounts.write_text(MOUNTS + "42 32 0:44 /subdir /other ro - " + mounted + "\n")
                self.verify(25)
        for invalid in ("", "truncated\n"):
            self.mounts.write_text(invalid)
            self.verify(25)

    def test_receipt_published_only_after_fresh_boot_with_unchanged_config(self):
        confirm = re.search(r"'confirm fresh dedicated distro boot' @'\n(.*?)\n'@", INSTALLER, re.S)[1]
        previous = self.root / "previous-instance"
        current = Path("/proc/sys/kernel/random/boot_id").read_text() + os.readlink("/proc/1/ns/pid") + "\n"
        task = confirm.replace("/etc/stockfish-wsl-isolation", str(self.root)).replace(
            "/etc/wsl.conf", str(self.conf)).replace("$(cat /proc/1/comm)", "systemd")
        for same_instance, fresh_config, interop, ok in (
                (True, True, False, False), (False, False, False, False),
                (False, True, True, False), (False, True, False, True)):
            with self.subTest(same=same_instance, fresh=fresh_config, interop=interop):
                self.apply(POLICY)
                self.stamp.rename(self.root / "pending")
                previous.write_text(current if same_instance else "previous boot\npid:[123]\n")
                if not fresh_config:
                    self.conf.write_text(POLICY + "# modified\n")
                env = dict(self.env, WSL_INTEROP="live") if interop else self.env
                run = subprocess.run(["bash", "-c", task], env=env, capture_output=True)
                self.assertEqual(run.returncode == 0, ok, run.stderr)
                self.assertEqual(self.stamp.exists(), ok)

    def test_old_install_requires_verification_and_cached_install_rechecks(self):
        check = embedded("checkStamp").replace("/etc/wsl.conf", str(self.conf)).replace(
            "/etc/stockfish-wsl-isolation/applied", str(self.stamp))
        for expected in (0, 42):
            run = subprocess.run(["bash", "-c", check], env=self.env)
            self.assertEqual(run.returncode, expected)
            self.stamp.write_bytes(b"")
        self.assertIn("ExecStartPre=/bin/bash /opt/stockfish-lab/research-lab/dedicated-worker/"
                      "check-wsl-isolation.sh", (WORKER / "install-guest.sh").read_text())
        self.assertLess(INSTALLER.index("'verify live dedicated distro isolation'"),
                        INSTALLER.index("try { Invoke-GuestBytes '/usr/local/libexec/stockfish-worker-set-credential'"))
