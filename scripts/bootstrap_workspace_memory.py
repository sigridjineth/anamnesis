#!/usr/bin/env python3
from __future__ import annotations

import sys

from anamnesis.bootstrap import main


if __name__ == "__main__":
    raise SystemExit(main(["--skip-sidecar-rebuild", *sys.argv[1:]]))
