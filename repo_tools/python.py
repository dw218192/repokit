"""PythonTool — run Python in the repo venv."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from .core import RepoTool, ToolContext


class PythonTool(RepoTool):
    name = "python"
    help = "Run Python in the repo tooling environment"

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        raise SystemExit(subprocess.call([sys.executable, *ctx.passthrough_args]))
