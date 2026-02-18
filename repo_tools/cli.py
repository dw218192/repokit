"""Entry point: main(), click group, tool discovery, override resolution."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any

import click

from .core import (
    RepoTool,
    detect_platform_identifier,
    load_config,
    logger,
    normalize_build_type,
    register_tool,
    resolve_filters,
    resolve_tokens,
)

# ── Tool Discovery ───────────────────────────────────────────────────


def _discover_tools_from_path(
    namespace_path: list[str],
    package_name: str,
) -> list[RepoTool]:
    """Discover RepoTool subclasses from namespace package portions."""
    tools: list[RepoTool] = []
    for module_info in pkgutil.iter_modules(namespace_path):
        name = module_info.name
        if name.startswith("_") or name in ("cli", "core"):
            continue
        try:
            module = importlib.import_module(f"{package_name}.{name}")
        except Exception as exc:
            logger.warning(f"Failed to import {package_name}.{name}: {exc}")
            continue

        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is RepoTool or not issubclass(cls, RepoTool):
                continue
            # For regular modules, class must be defined there
            # For packages, class can be re-exported from __init__.py
            if not module_info.ispkg and cls.__module__ != module.__name__:
                continue
            if module_info.ispkg and not cls.__module__.startswith(module.__name__):
                continue
            try:
                tool = cls()
            except Exception as exc:
                logger.warning(
                    f"Skipping tool '{cls.__name__}' due to init error: {exc}"
                )
                continue
            if not tool.name:
                logger.warning(f"Skipping tool '{cls.__name__}' with empty name")
                continue
            tools.append(tool)
    return tools


def _resolve_tools(framework_tools: list[RepoTool], project_tools: list[RepoTool]) -> list[RepoTool]:
    """Merge framework and project tools; project wins on name collision."""
    by_name: dict[str, RepoTool] = {t.name: t for t in framework_tools}
    for t in project_tools:
        by_name[t.name] = t  # project wins
    return sorted(by_name.values(), key=lambda t: t.name)


# ── Dimension Tokens → Click Options ────────────────────────────────


def _get_dimension_tokens(config: dict[str, Any]) -> dict[str, list[str]]:
    """Extract dimension tokens (lists) from config."""
    dims: dict[str, list[str]] = {}
    for key, value in config.get("tokens", {}).items():
        if isinstance(value, list) and value:
            dims[key] = [str(v) for v in value]
    return dims


def _auto_detect_dimension(name: str) -> str | None:
    """Auto-detect a default for known dimension names."""
    if name == "platform":
        return detect_platform_identifier()
    if name == "build_type":
        return "Debug"
    return None


# ── Click Command Builder ────────────────────────────────────────────


def _make_tool_command(
    tool: RepoTool,
    dimensions: dict[str, list[str]],
) -> click.Command:
    """Build a click command for a tool."""

    @click.pass_context
    def callback(ctx: click.Context, **kwargs: Any) -> None:
        tokens = ctx.obj["tokens"]
        config = ctx.obj["config"]

        # Merge: defaults < tokens < config < CLI
        tool_config = config.get(tool.name, {})
        if not isinstance(tool_config, dict):
            tool_config = {}

        defaults = tool.default_args(tokens)
        args: dict[str, Any] = {**defaults}
        args.update(tokens)

        # Config values as defaults
        for k, v in tool_config.items():
            if k not in kwargs or kwargs[k] is None:
                args[k] = v

        # CLI values override
        for k, v in kwargs.items():
            if v is not None:
                args[k] = v

        # Passthrough args
        args["passthrough_args"] = list(ctx.args) if ctx.args else []

        tool.execute(args)

    cmd = click.Command(
        name=tool.name,
        help=tool.help,
        callback=callback,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )

    # Let the tool add its own options
    cmd = tool.setup(cmd)

    return cmd


# ── Main CLI Group ───────────────────────────────────────────────────


def _build_cli(
    workspace_root: str | None = None,
    project_tool_dirs: list[str] | None = None,
) -> click.Group:
    """Build the top-level click group with all discovered tools."""

    @click.group()
    @click.option(
        "--workspace-root",
        type=click.Path(exists=True),
        default=workspace_root,
        help="Repository root directory",
    )
    @click.pass_context
    def cli(ctx: click.Context, workspace_root: str, **dim_kwargs: Any) -> None:
        ctx.ensure_object(dict)

        if workspace_root is None:
            workspace_root = str(Path.cwd())

        config = load_config(workspace_root)

        # Resolve dimension values from config tokens or fallback defaults
        dimensions = _get_dimension_tokens(config)
        dim_values: dict[str, str] = {}
        for dim_name in dimensions:
            cli_key = dim_name.replace("-", "_")
            val = dim_kwargs.get(cli_key)
            if val:
                dim_values[dim_name] = val
            else:
                auto = _auto_detect_dimension(dim_name)
                if auto:
                    dim_values[dim_name] = auto
                else:
                    dim_values[dim_name] = dimensions[dim_name][0]

        # Handle fallback --platform/--build-type when not in config tokens
        if "platform" not in dimensions:
            val = dim_kwargs.get("platform")
            dim_values["platform"] = val if val else detect_platform_identifier()

        if "build_type" not in dimensions:
            val = dim_kwargs.get("build_type")
            dim_values["build_type"] = normalize_build_type(val) if val else "Debug"

        # Normalize build_type
        if "build_type" in dim_values:
            dim_values["build_type"] = normalize_build_type(dim_values["build_type"])

        # Apply @filter resolution
        filtered_config = resolve_filters(config, dim_values)

        # Resolve tokens
        tokens = resolve_tokens(workspace_root, filtered_config, dim_values)

        ctx.obj["workspace_root"] = workspace_root
        ctx.obj["config"] = filtered_config
        ctx.obj["tokens"] = tokens
        ctx.obj["dimensions"] = dim_values

    # Load config early to get dimensions for global flags
    ws = workspace_root or str(Path.cwd())
    config = load_config(ws)
    dimensions = _get_dimension_tokens(config)

    # Add dimension flags to the group
    for dim_name, choices in dimensions.items():
        flag_name = f"--{dim_name.replace('_', '-')}"
        cli = click.option(
            flag_name,
            type=click.Choice(choices, case_sensitive=False),
            default=None,
            help=f"{dim_name} selection (auto-detected by default)",
        )(cli)

    # If no dimension tokens in config, add default --platform and --build-type
    if "platform" not in dimensions:
        cli = click.option(
            "--platform",
            default=None,
            help="Platform identifier (auto-detected by default)",
        )(cli)

    if "build_type" not in dimensions:
        cli = click.option(
            "--build-type",
            type=click.Choice(["Debug", "Release", "RelWithDebInfo", "MinSizeRel"], case_sensitive=False),
            default=None,
            help="Build configuration type (default: Debug)",
        )(cli)

    # Discover framework tools
    import repo_tools as rt_pkg

    framework_tools = _discover_tools_from_path(
        list(rt_pkg.__path__), rt_pkg.__name__
    )

    # Discover project tools (if sys.path includes project tool dirs)
    project_tools: list[RepoTool] = []
    if project_tool_dirs:
        for tool_dir in project_tool_dirs:
            tool_path = Path(tool_dir)
            if tool_path.exists() and str(tool_dir) not in sys.path:
                sys.path.insert(0, str(tool_dir))
        # Re-import to pick up namespace portions
        importlib.invalidate_caches()
        import repo_tools as rt_refreshed

        all_tools = _discover_tools_from_path(
            list(rt_refreshed.__path__), rt_refreshed.__name__
        )
        # Separate: tools from project dirs vs framework
        framework_modules = {t.__class__.__module__ for t in framework_tools}
        for t in all_tools:
            if t.__class__.__module__ not in framework_modules and t.name not in {
                pt.name for pt in project_tools
            }:
                project_tools.append(t)

    tools = _resolve_tools(framework_tools, project_tools)

    # Register all resolved tools in the global registry (for invoke_tool)
    for tool in tools:
        register_tool(tool)
        cmd = _make_tool_command(tool, dimensions)
        cli.add_command(cmd)

    return cli


def main() -> None:
    """CLI entry point. Called by the generated ``./repo`` shim.

    The shim sets PYTHONPATH to include the framework directory and runs::

        python -m repo_tools.cli --workspace-root /path/to/project ...
    """
    # --workspace-root can come from argv or be derived from this script's location
    workspace_root = None

    # Check for --workspace-root in sys.argv before click parses
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--workspace-root" and i < len(sys.argv) - 1:
            workspace_root = sys.argv[i + 1]
            break
        if arg.startswith("--workspace-root="):
            workspace_root = arg.split("=", 1)[1]
            break

    # Fallback: derive from script location
    # When installed as submodule at tools/framework/repo_tools/cli.py
    # workspace root is: cli.py -> repo_tools -> framework -> tools -> project_root
    if workspace_root is None:
        script_path = Path(__file__).resolve()
        candidate = script_path.parent.parent.parent.parent
        if (candidate / "config.yaml").exists():
            workspace_root = str(candidate)

    # Discover project tool dirs
    project_tool_dirs: list[str] = []
    if workspace_root:
        project_tools_dir = Path(workspace_root) / "tools" / "project_tools"
        if project_tools_dir.exists():
            project_tool_dirs.append(str(project_tools_dir))

    cli = _build_cli(
        workspace_root=workspace_root,
        project_tool_dirs=project_tool_dirs,
    )
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
