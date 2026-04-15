"""Entry point: main(), click group, tool discovery, override resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click

from .core import (
    RepoTool,
    ToolContext,
    _resolve_cfg_reference,
    auto_register_config_tools,
    discover_tools,
    ensure_project_tools_on_path,
    load_config,
    logger,
    register_tool,
    resolve_filters,
    resolve_tool_config,
    resolve_tokens,
)


def _resolve_tools(framework_tools: list[RepoTool], project_tools: list[RepoTool]) -> list[RepoTool]:
    """Merge framework and project tools; project wins on name collision."""
    by_name: dict[str, RepoTool] = {t.name: t for t in framework_tools}
    for t in project_tools:
        by_name[t.name] = t  # project wins
    return sorted(by_name.values(), key=lambda t: t.name)


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


# ── Click Command Builder ────────────────────────────────────────────


def _build_tool_context(ctx_obj: dict[str, Any], tool_name: str) -> ToolContext:
    """Build a ToolContext from the click context obj dict."""
    config = ctx_obj["config"]
    tool_config = config.get(tool_name, {})
    if isinstance(tool_config, str):
        tool_config = _resolve_cfg_reference(tool_config, config)
    if not isinstance(tool_config, dict):
        tool_config = {}
    tool_config = resolve_tool_config(tool_config, ctx_obj["tokens"], config)
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
) -> click.BaseCommand:
    """Build a click command (or group) for a tool."""

    is_group = type(tool).register_subcommands is not RepoTool.register_subcommands

    @click.pass_context
    def callback(ctx: click.Context, **kwargs: Any) -> None:
        if is_group and ctx.invoked_subcommand is not None:
            return

        # Extract dimension overrides from subcommand-level flags
        dim_overrides: dict[str, str] = {}
        for dim_name in dimensions:
            cli_key = dim_name.replace("-", "_")
            val = kwargs.pop(cli_key, None)
            if val is not None:
                dim_overrides[dim_name] = val

        if dim_overrides:
            updated_dims = {**ctx.obj["dimensions"], **dim_overrides}
            config = resolve_filters(load_config(ctx.obj["workspace_root"]), updated_dims)
            tokens = resolve_tokens(ctx.obj["workspace_root"], config, updated_dims)
            ctx.obj["dimensions"] = updated_dims
            ctx.obj["config"] = config
            ctx.obj["tokens"] = tokens

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

    ctx_settings = {"ignore_unknown_options": True, "allow_extra_args": True}

    if is_group:
        cmd: click.BaseCommand = click.Group(
            name=tool.name,
            help=tool.help,
            callback=callback,
            invoke_without_command=True,
            context_settings=ctx_settings,
        )
    else:
        cmd = click.Command(
            name=tool.name,
            help=tool.help,
            callback=callback,
            context_settings=ctx_settings,
        )

    # Add dimension options so they work after the subcommand name too
    for dim_name, choices in dimensions.items():
        flag_name = f"--{dim_name.replace('_', '-')}"
        cmd = click.option(
            flag_name,
            type=click.Choice(choices, case_sensitive=False),
            default=None,
            help=f"{dim_name} selection",
        )(cmd)

    # Let the tool add its own options
    cmd = tool.setup(cmd)

    # Let the tool register subcommands on the group
    if is_group:
        tool.register_subcommands(cmd)  # type: ignore[arg-type]

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
        config = early_config if workspace_root == ws else load_config(workspace_root)

        # Resolve dimension values from config tokens or fallback defaults
        dimensions = _get_dimension_tokens(config)
        dim_values: dict[str, str] = {}
        for dim_name in dimensions:
            cli_key = dim_name.replace("-", "_")
            val = dim_kwargs.get(cli_key)
            dim_values[dim_name] = val if val else dimensions[dim_name][0]

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
            help=f"{dim_name} selection",
        )(cli)

    # Add project tool dirs to sys.path BEFORE any imports so namespace
    # package discovery sees both framework and project portions.
    if project_tool_dirs:
        ensure_project_tools_on_path(project_tool_dirs)

    import repo_tools as rt_pkg

    # Discover all tools from merged namespace package
    all_tools = discover_tools(
        list(rt_pkg.__path__), rt_pkg.__name__
    )

    # Separate into framework vs project tools based on file location
    framework_tools: list[RepoTool] = []
    project_tools: list[RepoTool] = []
    project_prefixes = tuple(os.path.abspath(d) for d in (project_tool_dirs or []))
    for t in all_tools:
        mod = sys.modules.get(t.__class__.__module__)
        mod_file = getattr(mod, "__file__", "") or ""
        if project_prefixes and os.path.abspath(mod_file).startswith(project_prefixes):
            project_tools.append(t)
        else:
            framework_tools.append(t)

    tools = _resolve_tools(framework_tools, project_tools)

    # Config steps override framework tools but not project tools.
    project_names = {t.name for t in project_tools}
    auto_tools = auto_register_config_tools(early_config, project_names)
    if auto_tools:
        auto_names = {t.name for t in auto_tools}
        tools = [t for t in tools if t.name not in auto_names]
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

    # Check for --workspace-root in sys.argv before click parses.
    # Take the last occurrence so user overrides beat the shim's hardcoded value.
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--workspace-root" and i < len(sys.argv) - 1:
            workspace_root = sys.argv[i + 1]
        elif arg.startswith("--workspace-root="):
            workspace_root = arg.split("=", 1)[1]

    # Fallback: cwd (workspace_root stays None → defaults to cwd in _build_cli)

    # Discover project tool dirs
    project_tool_dirs: list[str] = []
    framework_root = Path(os.path.abspath(__file__)).parent.parent
    tools_dir = framework_root.parent
    if (tools_dir / "repo_tools").exists():
        project_tool_dirs.append(str(tools_dir))

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
