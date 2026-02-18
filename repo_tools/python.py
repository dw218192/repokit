"""PythonTool — run Python in the repo venv."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from .core import RepoTool


class PythonTool(RepoTool):
    name = "python"
    help = "Run Python in the repo tooling environment"

    def execute(self, args: dict[str, Any]) -> None:
        passthrough = args.get("passthrough_args", [])
        raise SystemExit(subprocess.call([sys.executable, *passthrough]))
