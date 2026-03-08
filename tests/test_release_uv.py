from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release_uv.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_uv_release.py"


class UvReleaseWorkflowTests(unittest.TestCase):
    def test_release_script_builds_without_publish(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anamnesis-release-script-") as tmp:
            out_dir = Path(tmp) / "dist"
            completed = subprocess.run(
                [sys.executable, str(RELEASE_SCRIPT), "--skip-verify", "--out-dir", str(out_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, msg=f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
            self.assertIn("Skipping publish", completed.stdout)
            self.assertTrue(list(out_dir.glob("uqa-*.whl")))
            self.assertTrue(list(out_dir.glob("uqa-*.tar.gz")))
            self.assertTrue(list(out_dir.glob("anamnesis-*.whl")))
            self.assertTrue(list(out_dir.glob("anamnesis-*.tar.gz")))

    def test_verify_script_builds_and_smokes_release_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anamnesis-verify-script-") as tmp:
            out_dir = Path(tmp) / "dist"
            completed = subprocess.run(
                [sys.executable, str(VERIFY_SCRIPT), "--out-dir", str(out_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, msg=f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
            self.assertIn("Verification completed successfully.", completed.stdout)
            self.assertTrue(list(out_dir.glob("uqa-*.whl")))
            self.assertTrue(list(out_dir.glob("uqa-*.tar.gz")))
            self.assertTrue(list(out_dir.glob("anamnesis-*.whl")))
            self.assertTrue(list(out_dir.glob("anamnesis-*.tar.gz")))


if __name__ == "__main__":
    unittest.main()
