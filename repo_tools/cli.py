"""Entry point: main(), click group, tool discovery, override resolution."""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import sys
from pathlib import Path
from typing import Any

import click

from .core import (
    RepoTool,
    ToolContext,
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
        if name.startswith("_") or name in ("cli", "core", "command_runner"):
            continue
        try:
            module = importlib.import_module(f"{package_name}.{name}")
        except ImportError as exc:
            logger.debug(f"Could not import {package_name}.{name}: {exc}")
            continue
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
            except ImportError as exc:
                logger.debug(
                    f"Skipping tool '{cls.__name__}' (missing dependency): {exc}"
                )
                continue
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


def _auto_register_config_tools(
    config: dict[str, Any],
    registered_names: set[str],
) -> list[RepoTool]:
    """Create CommandRunnerTools for eligible config sections.

    A section is eligible when it contains a ``steps`` or ``steps@*`` key.
    """
    from .command_runner import CommandRunnerTool

    _skip_sections = {"tokens", "repo"}
    auto_tools: list[RepoTool] = []

    for section_name, section_value in config.items():
        if section_name in _skip_sections:
            continue
        if section_name in registered_names:
            logger.debug(
                f"[auto-tool] '{section_name}': skipped — a RepoTool subclass is already registered."
            )
            continue
        if not isinstance(section_value, dict):
            continue

        has_steps = any(k.split("@", 1)[0] == "steps" for k in section_value)
        if not has_steps:
            continue

        tool = CommandRunnerTool()
        tool.name = section_name  # type: ignore[assignment]
        tool.help = f"Run {section_name} (from config.yaml)"
        auto_tools.append(tool)
        logger.debug(f"[auto-tool] '{section_name}': registered from config.yaml.")

    return auto_tools


# ── Dimension Tokens → Click Options ────────────────────────────────


def _get_dimension_tokens(config: dict[str, Any]) -> dict[str, list[str]]:
    """Extract dimension tokens (lists) from config.repo.tokens."""
    repo_section = config.get("repo", {})
    if not isinstance(repo_section, dict):
        repo_section = {}
    dims: dict[str, list[str]] = {}
    for key, value in repo_section.get("tokens", {}).items():
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


def _build_tool_context(ctx_obj: dict[str, Any], tool_name: str) -> ToolContext:
    """Build a ToolContext from the click context obj dict."""
    config = ctx_obj["config"]
    tool_config = config.get(tool_name, {})
    if not isinstance(tool_config, dict):
        tool_config = {}
    return ToolContext(
        workspace_root=Path(ctx_obj["workspace_root"]),
        tokens=ctx_obj["tokens"],
        config=config,
        tool_config=tool_config,
        dimensions=ctx_obj["dimensions"],
        passthrough_args=[],
    )


def _make_tool_command(
    tool: RepoTool,
    dimensions: dict[str, list[str]],
) -> click.Command:
    """Build a click command for a tool."""

    @click.pass_context
    def callback(ctx: click.Context, **kwargs: Any) -> None:
        context = _build_tool_context(ctx.obj, tool.name)
        context = ToolContext(
            workspace_root=context.workspace_root,
            tokens=context.tokens,
            config=context.config,
            tool_config=context.tool_config,
            dimensions=context.dimensions,
            passthrough_args=list(ctx.args) if ctx.args else [],
        )

        # Merge: defaults < tool_config < CLI kwargs
        args: dict[str, Any] = {**tool.default_args(context.tokens)}
        for k, v in context.tool_config.items():
            if k not in kwargs or kwargs[k] is None:
                args[k] = v
        for k, v in kwargs.items():
            if v is not None:
                args[k] = v

        tool.execute(context, args)

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

    @click.group(context_settings={"help_option_names": ["-h", "--help"]})
    @click.option(
        "--workspace-root",
        type=click.Path(exists=True),
        default=workspace_root,
        hidden=True,  # Set by the ./repo shim; consumers never pass this directly.
    )
    @click.pass_context
    def cli(ctx: click.Context, workspace_root: str, **dim_kwargs: Any) -> None:
        ctx.ensure_object(dict)

        if workspace_root is None:
            workspace_root = str(Path.cwd())

        # Reuse early config if workspace hasn't changed
        if workspace_root == ws:
            config = early_config
        else:
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

        # Normalize build_type if it was configured as a dimension.
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
    early_config = load_config(ws)
    dimensions = _get_dimension_tokens(early_config)

    # Add dimension flags to the group
    for dim_name, choices in dimensions.items():
        flag_name = f"--{dim_name.replace('_', '-')}"
        cli = click.option(
            flag_name,
            type=click.Choice(choices, case_sensitive=False),
            default=None,
            help=f"{dim_name} selection (auto-detected by default)",
        )(cli)

    # Add project tool dirs to sys.path BEFORE any imports so namespace
    # package discovery sees both framework and project portions.
    if project_tool_dirs:
        for tool_dir in project_tool_dirs:
            tool_path = Path(tool_dir)
            bad_init = tool_path / "repo_tools" / "__init__.py"
            if bad_init.exists():
                logger.error(
                    f"Remove {bad_init} — it breaks namespace package "
                    f"merging and hides framework tools.  "
                    f"See README.md § Extending."
                )
                sys.exit(1)
            if tool_path.exists() and str(tool_dir) not in sys.path:
                sys.path.insert(0, str(tool_dir))
        importlib.invalidate_caches()

    import repo_tools as rt_pkg

    # Discover all tools from merged namespace package
    all_tools = _discover_tools_from_path(
        list(rt_pkg.__path__), rt_pkg.__name__
    )

    # Separate into framework vs project tools based on file location
    framework_tools: list[RepoTool] = []
    project_tools: list[RepoTool] = []
    project_prefixes = tuple(str(Path(d).resolve()) for d in (project_tool_dirs or []))
    for t in all_tools:
        mod = sys.modules.get(t.__class__.__module__)
        mod_file = getattr(mod, "__file__", "") or ""
        if project_prefixes and str(Path(mod_file).resolve()).startswith(project_prefixes):
            project_tools.append(t)
        else:
            framework_tools.append(t)

    tools = _resolve_tools(framework_tools, project_tools)

    # Auto-generate CommandRunnerTools for config sections not already covered.
    registered_names = {t.name for t in tools}
    auto_tools = _auto_register_config_tools(early_config, registered_names)
    if auto_tools:
        tools = sorted(tools + auto_tools, key=lambda t: t.name)

    # Feature-gating: hide tools whose feature is not enabled in repo.features.
    # When repo.features is absent, all features are implicitly enabled.
    repo_section = early_config.get("repo", {})
    if not isinstance(repo_section, dict):
        repo_section = {}
    features_value = repo_section.get("features")
    if features_value is not None:
        enabled_features = set(features_value)
        tools = [
            t for t in tools
            if not t.feature or t.feature in enabled_features
        ]

    # Register all resolved tools in the global registry (for invoke_tool)
    for tool in tools:
        register_tool(tool)
        custom_cmd = tool.create_click_command()
        if custom_cmd is not None:
            cli.add_command(custom_cmd)
        else:
            cmd = _make_tool_command(tool, dimensions)
            cli.add_command(cmd)

    return cli


def main() -> None:
    """CLI entry point. Called by the generated ``./repo`` shim.

    The shim sets PYTHONPATH to include the framework directory and runs::

        python -m repo_tools.cli --workspace-root /path/to/project ...
    """
    from colorama import init as colorama_init
    colorama_init()

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
        project_tools_dir = Path(workspace_root) / "tools"
        if (project_tools_dir / "repo_tools").exists():
            project_tool_dirs.append(str(project_tools_dir))

    cli = _build_cli(
        workspace_root=workspace_root,
        project_tool_dirs=project_tool_dirs,
    )
    if os.name != "nt" or os.environ.get("BASH_VERSION"):
        prog = "./repo"
    else:
        prog = ".\\repo.cmd"
    cli(prog_name=prog, standalone_mode=True)


if __name__ == "__main__":
    main()
