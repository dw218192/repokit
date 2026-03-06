"""Allow ``python -m repo_tools.agent.mcp <server>`` to launch a server."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            "Usage: python -m repo_tools.agent.mcp {coderabbit|lint|tickets|repo_cmd} [args...]",
            file=sys.stderr,
        )
        sys.exit(2)

    subcommand = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if subcommand == "coderabbit":
        from .coderabbit import main as sub_main
    elif subcommand == "lint":
        from .lint import main as sub_main
    elif subcommand == "tickets":
        from .tickets import main as sub_main
    elif subcommand == "repo_cmd":
        from .repo_cmd import main as sub_main
    else:
        print(f"Unknown MCP server: {subcommand!r}", file=sys.stderr)
        sys.exit(2)

    sub_main()


if __name__ == "__main__":
    main()
