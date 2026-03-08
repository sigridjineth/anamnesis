from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

import numpy as np


GETFLEX_VERSION = os.environ.get("ANAMNESIS_GETFLEX_VERSION", "0.5.0")
GETFLEX_WHEEL_URL = os.environ.get(
    "ANAMNESIS_GETFLEX_WHEEL_URL",
    f"https://github.com/damiandelmas/flex/releases/download/v{GETFLEX_VERSION}/getflex-{GETFLEX_VERSION}-py3-none-any.whl",
)


@dataclass(slots=True)
class GetFlexRuntime:
    workspace_root: Path

    @property
    def runtime_root(self) -> Path:
        return self.workspace_root / ".anamnesis" / "getflex" / GETFLEX_VERSION

    @property
    def venv_dir(self) -> Path:
        return self.runtime_root / "venv"

    @property
    def flex_home(self) -> Path:
        return self.workspace_root / ".flex"

    @property
    def python_bin(self) -> Path:
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    def ensure_installed(self) -> Path:
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("uv is required to bootstrap the managed getflex runtime")

        self.runtime_root.mkdir(parents=True, exist_ok=True)
        if not self.python_bin.exists():
            self._run([
                uv,
                "venv",
                str(self.venv_dir),
                "--python",
                sys.executable,
            ])
            self._run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(self.python_bin),
                    GETFLEX_WHEEL_URL,
                    "mcp>=1.0.0",
                ],
                env=self._base_env(),
            )
        else:
            result = self._run(
                [
                    str(self.python_bin),
                    "-c",
                    "from importlib.metadata import version; import mcp; print(version('getflex'))",
                ],
                env=self._base_env(),
                check=False,
            )
            if result.returncode != 0 or result.stdout.strip() != GETFLEX_VERSION:
                self._run(
                    [
                        uv,
                        "pip",
                        "install",
                        "--python",
                        str(self.python_bin),
                        GETFLEX_WHEEL_URL,
                        "mcp>=1.0.0",
                    ],
                    env=self._base_env(),
                )
        self._ensure_model()
        return self.python_bin

    def register_and_install_assets(self, *, cell_name: str, db_path: Path, description: str) -> None:
        code = textwrap.dedent(
            """
            import sqlite3
            import sys
            from pathlib import Path

            from flex.core import set_meta
            from flex.manage.install_presets import install_cell
            from flex.registry import register_cell
            from flex.views import install_views
            import flex

            db_path = Path(sys.argv[1]).resolve()
            cell_name = sys.argv[2]
            description = sys.argv[3]
            package_root = Path(flex.__file__).resolve().parent
            view_dir = package_root / 'modules' / 'claude_code' / 'stock' / 'views'
            register_cell(cell_name, db_path, cell_type='claude-code', description=description, corpus_path=db_path.parent)
            conn = sqlite3.connect(str(db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=30000')
            set_meta(conn, 'description', description)
            install_views(conn, view_dir)
            conn.close()
            install_cell(cell_name)
            """
        )
        self._run_python(code, str(db_path), cell_name, description)

    def encode_texts(
        self,
        texts: list[str],
        *,
        prefix: str = "search_document: ",
        matryoshka_dim: int = 128,
    ) -> list[bytes]:
        if not texts:
            return []

        with tempfile.TemporaryDirectory(prefix="anamnesis-flex-embed-") as tmpdir:
            input_path = Path(tmpdir) / "texts.json"
            output_path = Path(tmpdir) / "vectors.npy"
            input_path.write_text(
                json.dumps(
                    {
                        "texts": [str(text or "") for text in texts],
                        "prefix": prefix,
                        "matryoshka_dim": int(matryoshka_dim),
                    }
                )
            )
            code = textwrap.dedent(
                """
                import json
                import sys

                import numpy as np
                from flex.onnx.embed import get_model

                payload = json.loads(open(sys.argv[1], 'r', encoding='utf-8').read())
                model = get_model()
                vectors = model.encode(
                    payload['texts'],
                    prefix=payload.get('prefix', 'search_document: '),
                    matryoshka_dim=int(payload.get('matryoshka_dim', 128)),
                )
                np.save(sys.argv[2], np.asarray(vectors, dtype=np.float32))
                """
            )
            self._run_python(code, str(input_path), str(output_path))
            matrix = np.load(output_path)
        return [np.asarray(row, dtype=np.float32).tobytes() for row in matrix]

    def run_claude_code_enrichment(self, *, cell_name: str = "claude_code", db_path: Path) -> dict:
        code = textwrap.dedent(
            """
            import contextlib
            import io
            import json
            import sqlite3
            import sys
            from pathlib import Path

            import flex
            from flex.manage.install_presets import install_cell
            from flex.modules.claude_code.manage.enrich_summary import run as run_fingerprints
            from flex.modules.claude_code.manage.rebuild_all import (
                rebuild_community_labels,
                rebuild_delegation_graph,
                rebuild_file_graph,
                rebuild_source_graph,
                rebuild_warmup_types,
                reembed_sources,
            )
            from flex.views import install_views, regenerate_views

            db_path = Path(sys.argv[1]).resolve()
            cell_name = sys.argv[2]
            conn = sqlite3.connect(str(db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=30000')

            failures = []
            log_buffer = io.StringIO()
            with contextlib.redirect_stdout(log_buffer), contextlib.redirect_stderr(log_buffer):
                steps = [
                    ('warmup_types', rebuild_warmup_types),
                    ('source_pooling', reembed_sources),
                    ('source_graph', rebuild_source_graph),
                    ('file_graph', rebuild_file_graph),
                    ('delegation_graph', rebuild_delegation_graph),
                    ('fingerprints', run_fingerprints),
                    ('community_labels', rebuild_community_labels),
                ]
                for name, fn in steps:
                    try:
                        fn(conn)
                    except Exception as exc:  # pragma: no cover - surfaced to caller
                        failures.append({'step': name, 'error': str(exc)})

                view_dir = Path(flex.__file__).resolve().parent / 'modules' / 'claude_code' / 'stock' / 'views'
                try:
                    install_views(conn, view_dir)
                    regenerate_views(conn, views={'messages': 'chunk', 'sessions': 'source'})
                    conn.commit()
                    install_cell(cell_name)
                finally:
                    conn.close()

            sys.stdout.write(json.dumps({'failures': failures, 'log': log_buffer.getvalue()[-4000:]}, default=str))
            """
        )
        result = self._run_python(code, str(db_path), cell_name)
        return json.loads(result.stdout or "{}")

    def execute_cli_query(self, *, cell_name: str, query: str) -> str:
        code = textwrap.dedent(
            """
            import sys
            from flex.cli import _open_cell_for_search
            from flex.mcp_server import execute_query

            db = _open_cell_for_search(sys.argv[1])
            try:
                sys.stdout.write(execute_query(db, sys.argv[2]))
            finally:
                db.close()
            """
        )
        result = self._run_python(code, cell_name, query)
        return result.stdout

    def execute_mcp_query(self, *, cell_name: str, query: str, params: dict | None = None) -> str:
        code = textwrap.dedent(
            """
            import json
            import sys

            from flex.mcp_server import (
                _GATE_FORCE_CHAR_LIMIT,
                _GATE_FORCE_LIMIT,
                _GATE_TOKEN_LIMIT,
                _execute_cell_query,
                _gate_response,
                _token_header,
            )

            query = sys.argv[2]
            params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
            if params and query.lstrip('!').startswith('@'):
                param_str = ' '.join(f"{k}={v}" for k, v in params.items())
                query = f"{query} {param_str}"

            force = False
            if query.startswith('!'):
                force = True
                query = query[1:].lstrip()

            result = _execute_cell_query(sys.argv[1], query)
            row_count, est_tokens, header = _token_header(result)
            if not force and est_tokens > _GATE_TOKEN_LIMIT:
                sys.stdout.write(_gate_response(result, header, row_count, est_tokens))
            elif force and est_tokens > _GATE_FORCE_LIMIT:
                truncated = result[:_GATE_FORCE_CHAR_LIMIT]
                warning = "\\n\\n[truncated at ~%dK tokens — add LIMIT to query]" % (_GATE_FORCE_LIMIT // 1000)
                sys.stdout.write(header + "\\n" + truncated + warning)
            else:
                sys.stdout.write(header + "\\n" + result)
            """
        )
        result = self._run_python(code, cell_name, query, json.dumps(params or {}, default=str))
        return result.stdout

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["FLEX_HOME"] = str(self.flex_home)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    def _run_python(self, code: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.ensure_installed()
        return self._run(
            [str(self.python_bin), "-c", code, *args],
            env=self._base_env(),
            check=check,
        )

    def _ensure_model(self) -> None:
        self._run(
            [
                str(self.python_bin),
                "-c",
                "from flex.onnx.fetch import model_ready, download_model; "
                "import sys; "
                "download_model(force=False) if not model_ready() else None; "
                "sys.stdout.write('ok')",
            ],
            env=self._base_env(),
            check=True,
        )

    @staticmethod
    def _run(cmd: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            env=env,
            text=True,
            capture_output=True,
            check=check,
        )
