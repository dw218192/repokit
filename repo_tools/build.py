"""Default BuildTool — runs config command with token expansion."""

from __future__ import annotations

from .command_runner import CommandRunnerTool


class BuildTool(CommandRunnerTool):
    name = "build"
    help = "Build the project (runs command from config with token expansion)"
    config_hint = 'build:\n    command: "cmake --build {build_dir} --config {build_type}"'
