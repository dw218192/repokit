"""FormatTool — multi-backend code formatter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from .core import RepoTool, find_venv_executable, logger


_CLANG_FORMAT_EXTENSIONS = {".cpp", ".h", ".hpp", ".c", ".cc", ".cxx", ".hxx"}
_PYTHON_EXTENSIONS = {".py"}
_ALWAYS_EXCLUDE = {"_tools", "ext", ".git", ".vs", "build", "_build", "_logs", "node_modules"}


class FormatTool(RepoTool):
    name = "format"
    help = "Format source code"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--verify", is_flag=True, help="Check formatting without modifying files")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"verify": False}

    def execute(self, args: dict[str, Any]) -> None:
        root = Path(args.get("workspace_root", "."))
        verify = args.get("verify", False)
        backends = args.get("backends")

        if backends:
            self._run_configured_backends(root, backends, verify)
        else:
            self._run_auto_detect(root, verify, args)

    def _run_auto_detect(self, root: Path, verify: bool, args: dict[str, Any]) -> None:
        """Auto-detect formatters from repo contents."""
        # Check for clang-format
        clang_format_file = root / ".clang-format"
        if clang_format_file.exists():
            self._run_clang_format(root, verify, args)

    def _run_configured_backends(
        self, root: Path, backends: list[dict[str, Any]], verify: bool,
    ) -> None:
        for backend in backends:
            backend_type = backend.get("type", "")
            if backend_type == "clang-format":
                extensions = set(backend.get("extensions", _CLANG_FORMAT_EXTENSIONS))
                self._run_clang_format(root, verify, {}, extensions)
            elif backend_type == "python":
                tool_name = backend.get("tool", "ruff")
                self._run_python_formatter(root, verify, tool_name)
            else:
                logger.warning(f"Unknown format backend: {backend_type}")

    def _run_clang_format(
        self,
        root: Path,
        verify: bool,
        args: dict[str, Any],
        extensions: set[str] | None = None,
    ) -> None:
        if extensions is None:
            extensions = _CLANG_FORMAT_EXTENSIONS

        exclude_dirs = set(_ALWAYS_EXCLUDE)
        build_root = args.get("build_root")
        logs_root = args.get("logs_root")
        if build_root:
            exclude_dirs.add(Path(build_root).name)
        if logs_root:
            exclude_dirs.add(Path(logs_root).name)

        clang_format_exe = find_venv_executable("clang-format")
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
            logger.info("Running in verify mode (no files will be modified)")
            failed_files = []
            for file_path in source_files:
                original_content = file_path.read_text(encoding="utf-8", errors="replace")
                result = subprocess.run(
                    [clang_format_exe, f"--style=file:{clang_format_file}", str(file_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
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
        else:
            logger.info("Formatting files...")
            for file_path in source_files:
                try:
                    subprocess.run(
                        [clang_format_exe, "-i", f"--style=file:{clang_format_file}", str(file_path)],
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr if e.stderr else str(e)
                    logger.error(f"Failed to format {file_path}: {error_msg}")
                    sys.exit(1)
            logger.info(f"Successfully formatted {len(source_files)} file(s)")

    def _run_python_formatter(self, root: Path, verify: bool, tool_name: str) -> None:
        exe = find_venv_executable(tool_name)
        cmd = [exe, "check", str(root)] if verify else [exe, "format", str(root)]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            if verify:
                logger.error("Python files are not properly formatted")
                sys.exit(1)
