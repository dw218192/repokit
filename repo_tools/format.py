"""FormatTool — multi-backend code formatter."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from .core import RepoTool, ToolContext, find_venv_executable, logger


_CLANG_FORMAT_EXTENSIONS = {".cpp", ".h", ".hpp", ".c", ".cc", ".cxx", ".hxx"}
_PYTHON_EXTENSIONS = {".py"}
_ALWAYS_EXCLUDE = {"_tools", "ext", ".git", ".vs", "build", "_build", "_logs", "node_modules"}
_BATCH_SIZE = 200  # max files per clang-format invocation (Windows cmdline limit)


class FormatTool(RepoTool):
    name = "format"
    help = "Format source code"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--verify", is_flag=True, help="Check formatting without modifying files")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"verify": False}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        root = ctx.workspace_root
        verify = args.get("verify", False)
        backends = args.get("backends")

        if backends:
            self._run_configured_backends(root, backends, verify, ctx)
        else:
            self._run_auto_detect(root, verify, ctx)

    def _run_auto_detect(self, root: Path, verify: bool, ctx: ToolContext) -> None:
        """Auto-detect formatters from repo contents."""
        clang_format_file = root / ".clang-format"
        if clang_format_file.exists():
            self._run_clang_format(root, verify, ctx)

        # Python auto-detection
        if any((root / marker).exists() for marker in ("pyproject.toml", "setup.py", "ruff.toml")):
            self._run_python_formatter(root, verify, "ruff")

    def _run_configured_backends(
        self, root: Path, backends: list[dict[str, Any]], verify: bool, ctx: ToolContext,
    ) -> None:
        for backend in backends:
            backend_type = backend.get("type", "")
            if backend_type == "clang-format":
                extensions = set(backend.get("extensions", _CLANG_FORMAT_EXTENSIONS))
                self._run_clang_format(root, verify, ctx, extensions)
            elif backend_type == "python":
                tool_name = backend.get("tool", "ruff")
                self._run_python_formatter(root, verify, tool_name)
            else:
                logger.warning(f"Unknown format backend: {backend_type}")

    def _run_clang_format(
        self,
        root: Path,
        verify: bool,
        ctx: ToolContext,
        extensions: set[str] | None = None,
    ) -> None:
        if extensions is None:
            extensions = _CLANG_FORMAT_EXTENSIONS

        exclude_dirs = set(_ALWAYS_EXCLUDE)
        build_root = ctx.tokens.get("build_root")
        logs_root = ctx.tokens.get("logs_root")
        if build_root:
            exclude_dirs.add(Path(build_root).name)
        if logs_root:
            exclude_dirs.add(Path(logs_root).name)

        clang_format_exe = find_venv_executable("clang-format")

        # Verify the executable actually exists
        if not shutil.which(clang_format_exe):
            logger.error(
                f"clang-format not found at '{clang_format_exe}'. "
                "Install it or add it to PATH."
            )
            sys.exit(1)

        clang_format_file = root / ".clang-format"
        if not clang_format_file.exists():
            logger.error(f".clang-format not found at {clang_format_file}")
            sys.exit(1)

        source_files = []
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in extensions:
                parts = path.parts
                if not any(excluded in parts for excluded in exclude_dirs):
                    source_files.append(path)

        if not source_files:
            logger.warning("No source files found to format")
            return

        logger.info(f"Found {len(source_files)} source files to format")

        if verify:
            self._clang_format_verify(clang_format_exe, clang_format_file, source_files)
        else:
            self._clang_format_inplace(clang_format_exe, clang_format_file, source_files)

    def _clang_format_verify(
        self, exe: str, style_file: Path, files: list[Path],
    ) -> None:
        logger.info("Running in verify mode (no files will be modified)")
        failed_files = []

        # Try --dry-run --Werror first (clang-format 10+)
        test = subprocess.run(
            [exe, "--dry-run", "--Werror", "--style=file", str(files[0])],
            capture_output=True, text=True,
        )
        use_dry_run = test.returncode in (0, 1)  # 0=ok, 1=diff found; not "unknown flag"

        if use_dry_run:
            for batch_start in range(0, len(files), _BATCH_SIZE):
                batch = files[batch_start:batch_start + _BATCH_SIZE]
                result = subprocess.run(
                    [exe, "--dry-run", "--Werror", f"--style=file:{style_file}"]
                    + [str(f) for f in batch],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                if result.returncode != 0:
                    # Parse stderr for file names
                    for line in result.stderr.splitlines():
                        for f in batch:
                            if str(f) in line:
                                failed_files.append(f)
                                break
        else:
            # Fallback: per-file comparison
            for file_path in files:
                original_content = file_path.read_text(encoding="utf-8", errors="replace")
                result = subprocess.run(
                    [exe, f"--style=file:{style_file}", str(file_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                )
                if result.returncode != 0:
                    failed_files.append(file_path)
                    logger.error(f"Failed to format {file_path}: {result.stderr}")
                    continue
                if original_content != result.stdout:
                    failed_files.append(file_path)
                    logger.error(f"File is not properly formatted: {file_path}")

        if failed_files:
            logger.error(f"{len(failed_files)} file(s) are not properly formatted")
            sys.exit(1)
        else:
            logger.info("All files are properly formatted")

    def _clang_format_inplace(
        self, exe: str, style_file: Path, files: list[Path],
    ) -> None:
        logger.info("Formatting files...")
        for batch_start in range(0, len(files), _BATCH_SIZE):
            batch = files[batch_start:batch_start + _BATCH_SIZE]
            try:
                subprocess.run(
                    [exe, "-i", f"--style=file:{style_file}"]
                    + [str(f) for f in batch],
                    check=True, capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                )
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr if e.stderr else str(e)
                logger.error(f"Failed to format batch: {error_msg}")
                sys.exit(1)
        logger.info(f"Successfully formatted {len(files)} file(s)")

    def _run_python_formatter(self, root: Path, verify: bool, tool_name: str) -> None:
        exe = find_venv_executable(tool_name)
        if not shutil.which(exe):
            logger.error(
                f"{tool_name} not found at '{exe}'. "
                "Install it or add it to PATH."
            )
            sys.exit(1)

        if verify:
            cmd = [exe, "format", "--check", str(root)]
        else:
            cmd = [exe, "format", str(root)]

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            if verify:
                logger.error("Python files are not properly formatted")
            else:
                logger.error("Python formatting failed")
            sys.exit(1)
