---
name: repo-tools
description: Discover and run this project's `./repo` commands (build, test, format, lint, etc.) via the `repo_run` MCP tool instead of invoking the CLI through Bash.
---

The repokit framework exposes each project tool as a `./repo <name>`
subcommand. The exact set of registered commands is project-defined and
discoverable at runtime.

## Discovery

Run `./repo --help` (Bash) to enumerate the commands registered in the
current project. Common command names you should expect to see:

- `build` — compile / build the project
- `test` — run the test suite
- `test-cov` — run tests with coverage
- `format` — apply code formatters
- `lint` — run linters
- `clean` — remove build artifacts

Other commands may exist depending on the project's `config.yaml`. Always
check `./repo --help` instead of assuming.

## Invocation

The agent has access to a `repo_run` MCP tool that targets the correct
working directory (main workspace or worktree) automatically. **Prefer it
over invoking `./repo` via Bash** — `repo_run` produces structured output,
auto-logs to `_build/logs/`, and avoids quoting / shim issues on Windows.

Example: `repo_run(command="test")` runs the project's test suite.
