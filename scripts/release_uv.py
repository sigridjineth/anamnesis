#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_uv_release.py"


def _shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def run(args: list[str], *, cwd: Path = REPO_ROOT) -> None:
    print(f"$ {_shell_join(args)}")
    completed = subprocess.run(args, cwd=cwd, check=False, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {_shell_join(args)}")


def artifact_paths(out_dir: Path) -> list[Path]:
    patterns = ["uqa-*", "anamnesis-*"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(out_dir.glob(pattern)))
    return [path for path in paths if path.suffix in {".whl", ".gz"}]


def publish_order(out_dir: Path) -> list[Path]:
    ordered: list[Path] = []
    for package in ("uqa", "anamnesis"):
        ordered.extend(sorted(out_dir.glob(f"{package}-*.tar.gz")))
        ordered.extend(sorted(out_dir.glob(f"{package}-*.whl")))
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Anamnesis + UQA release artifacts with uv, optionally verify them, and publish them when explicitly requested."
    )
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "dist"), help="Directory where uv build should write artifacts.")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to hand to uv build/uv venv (defaults to the current interpreter).",
    )
    parser.add_argument("--skip-verify", action="store_true", help="Skip the clean-environment install + smoke verification step.")
    parser.add_argument("--with-mcp", action="store_true", help="Pass --with-mcp through to the verification step.")
    parser.add_argument("--publish", action="store_true", help="Upload artifacts with uv publish after build/verification.")
    parser.add_argument("--index", help="Pass --index through to uv publish.")
    parser.add_argument("--publish-url", help="Pass --publish-url through to uv publish.")
    parser.add_argument("--check-url", help="Pass --check-url through to uv publish.")
    parser.add_argument(
        "--trusted-publishing",
        choices=["automatic", "always", "never"],
        help="Pass --trusted-publishing through to uv publish.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    run(["uv", "build", "--all-packages", "--out-dir", str(out_dir), "--python", args.python])

    if not args.skip_verify:
        verify_args = [sys.executable, str(VERIFY_SCRIPT), "--skip-build", "--out-dir", str(out_dir), "--python", args.python]
        if args.with_mcp:
            verify_args.append("--with-mcp")
        run(verify_args)

    files = artifact_paths(out_dir)
    if not files:
        raise FileNotFoundError(f"no built artifacts found in {out_dir}")

    if not args.publish:
        print("Skipping publish. Re-run with --publish after configuring UV_PUBLISH_TOKEN or trusted publishing.")
        print("Artifacts ready:")
        for path in files:
            print(f"- {path}")
        return 0

    for path in publish_order(out_dir):
        publish_args = ["uv", "publish"]
        if args.index:
            publish_args.extend(["--index", args.index])
        if args.publish_url:
            publish_args.extend(["--publish-url", args.publish_url])
        if args.check_url:
            publish_args.extend(["--check-url", args.check_url])
        if args.trusted_publishing:
            publish_args.extend(["--trusted-publishing", args.trusted_publishing])
        publish_args.append(str(path))
        run(publish_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
