#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def run(args: list[str], *, cwd: Path = REPO_ROOT, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    print(f"$ {_shell_join(args)}")
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=capture_output,
        text=True,
    )
    if capture_output:
        if completed.stdout:
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
        if completed.stderr:
            print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {_shell_join(args)}")
    return completed


def venv_executable(venv_dir: Path, name: str) -> Path:
    scripts_dir = "Scripts" if sys.platform.startswith("win") else "bin"
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return venv_dir / scripts_dir / f"{name}{suffix}"


def latest_artifact(out_dir: Path, pattern: str) -> Path:
    matches = sorted(out_dir.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"no artifacts matched {pattern!r} in {out_dir}")
    return matches[-1]


def verify_help(executable: Path, expected: str) -> None:
    completed = run([str(executable), "--help"], capture_output=True)
    if expected not in completed.stdout:
        raise AssertionError(f"{executable.name} --help did not contain expected text: {expected!r}")


def smoke_ingest(venv_dir: Path) -> None:
    ingest_exe = venv_executable(venv_dir, "anamnesis-ingest")
    python_exe = venv_executable(venv_dir, "python")

    with tempfile.TemporaryDirectory(prefix="anamnesis-uv-smoke-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "anamnesis.db"
        payload_path = tmp_path / "payload.json"
        payload_path.write_text(
            json.dumps(
                {
                    "event": "UserPromptSubmit",
                    "session_id": "uv-release-smoke",
                    "project": "uv-release-smoke",
                    "timestamp": "2026-03-08T00:00:00Z",
                    "prompt": "hello release verification",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        run(
            [
                str(ingest_exe),
                "--agent",
                "claude",
                "--db",
                str(db_path),
                "--input",
                str(payload_path),
            ]
        )

        query = (
            "import sqlite3\n"
            "db = sqlite3.connect(r'" + str(db_path) + "')\n"
            "row = db.execute('SELECT COUNT(*), COALESCE(MAX(content), \"\") FROM events').fetchone()\n"
            "db.close()\n"
            "assert row[0] == 1, row\n"
            "assert 'hello release verification' in row[1], row\n"
            "print('ingest smoke ok')\n"
        )
        run([str(python_exe), "-c", query], capture_output=True)


def smoke_init(venv_dir: Path) -> None:
    init_exe = venv_executable(venv_dir, "anamnesis-init")

    with tempfile.TemporaryDirectory(prefix="anamnesis-init-smoke-") as tmp:
        workspace_root = Path(tmp) / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        codex_home = workspace_root / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        db_path = workspace_root / ".anamnesis" / "anamnesis.db"

        run(
            [
                str(init_exe),
                "--workspace-root",
                str(workspace_root),
                "--db-path",
                str(db_path),
                "--codex-home",
                str(codex_home),
            ]
        )

        expected_paths = [
            workspace_root / ".claude" / "skills" / "survey" / "SKILL.md",
            workspace_root / ".agents" / "skills" / "chronicle" / "SKILL.md",
            workspace_root / ".mcp.json",
            codex_home / "settings.json",
        ]
        missing = [str(path) for path in expected_paths if not path.exists()]
        if missing:
            raise AssertionError(f"anamnesis-init did not create expected files: {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build UQA + Anamnesis release artifacts with uv and verify they install and run in a clean uv-managed virtualenv."
    )
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "dist"), help="Directory where uv build should write artifacts.")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to hand to uv build/uv venv (defaults to the current interpreter).",
    )
    parser.add_argument("--skip-build", action="store_true", help="Skip uv build and reuse artifacts already present in --out-dir.")
    parser.add_argument(
        "--with-mcp",
        action="store_true",
        help="Also install the optional MCP dependency in the verification environment and smoke-test anamnesis-mcp --help.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_build:
        run(["uv", "build", "--all-packages", "--out-dir", str(out_dir), "--python", args.python])

    uqa_wheel = latest_artifact(out_dir, "uqa-*.whl")
    anamnesis_wheel = latest_artifact(out_dir, "anamnesis-*.whl")
    uqa_sdist = latest_artifact(out_dir, "uqa-*.tar.gz")
    anamnesis_sdist = latest_artifact(out_dir, "anamnesis-*.tar.gz")
    print(f"Verified artifacts exist:\n- {uqa_sdist}\n- {uqa_wheel}\n- {anamnesis_sdist}\n- {anamnesis_wheel}")

    with tempfile.TemporaryDirectory(prefix="anamnesis-uv-venv-") as tmp:
        venv_dir = Path(tmp) / "venv"
        run(["uv", "venv", "--python", args.python, str(venv_dir)])

        python_exe = venv_executable(venv_dir, "python")
        run(["uv", "pip", "install", "--python", str(python_exe), str(uqa_wheel), str(anamnesis_wheel)])

        verify_help(venv_executable(venv_dir, "anamnesis"), "Anamnesis — searchable shared memory for Claude, Codex, and OpenCode.")
        verify_help(venv_executable(venv_dir, "anamnesis-init"), "Write deployable Claude/Codex/OpenCode configuration for Anamnesis")
        verify_help(venv_executable(venv_dir, "anamnesis-ingest"), "Normalize agent hook payloads")
        verify_help(venv_executable(venv_dir, "anamnesis-codex-sync"), "Backfill Codex history")
        verify_help(venv_executable(venv_dir, "anamnesis-opencode-sync"), "Backfill OpenCode exported sessions")

        smoke_ingest(venv_dir)
        smoke_init(venv_dir)

        if args.with_mcp:
            run(["uv", "pip", "install", "--python", str(python_exe), "mcp>=1.0.0"])
            verify_help(venv_executable(venv_dir, "anamnesis-mcp"), "Run the Anamnesis MCP server")

    print("Verification completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
