---
id: SPIKE-1
title: Native permissions suffice to replace the custom allowlist
question: Do native `permissions.deny` rules + auto mode suffice to replace the custom allowlist (rules.py + check_bash), and where must the deny rules live?
method: Verified Claude Code permission capabilities against the official docs (via the claude-code-guide research pass) and inspected the generation layer that emits the native config.
findings: >
  Yes. (1) Auto mode is a real permission mode — a server-side classifier plus a
  user-configurable trusted-environment surface (`/auto-mode defaults`) — giving an
  unattended run a safe-but-autonomous option that did not exist when the allowlist
  was built. (2) `permissions.deny` rules are declarative and clear
  (`Edit(<glob>)`, `Write(<glob>)`). (3) Plugins CANNOT contribute permissions, so
  the deny rules must live in the project's `.claude/settings.json`, not the plugin.
  (4) Consequently the ~615 LOC custom allowlist surface (rules.py + manifest +
  check_bash/approve_mcp hooks) is unnecessary. The Phase-A generation layer already
  emits these denies + the ADR-immutability hook into `.claude/settings.json`
  (`repo_tools/agent/generate.py::_render_settings_json`), so the native replacement
  is in place before the allowlist is removed.
status: concluded
refs:
  - docs/adr/3-permissions-and-scope.md
  - repo_tools/agent/generate.py
---

## Investigation

ADR-3 decided "ship zero custom permission code," resting on two capability
claims: that auto mode exists with a usable classifier, and that
`permissions.deny` is declarative. Both were verified against the Claude Code
docs during the design review:

- **Auto mode** is a documented permission mode. Its classifier model and block
  rules are server-side/fixed; what the user configures is the trusted-environment
  surface. This is enough to satisfy ADR-3's requirement — a safe-but-autonomous
  default owned by the user — even though "tunable classifier" overstated it (ADR-3
  was corrected accordingly).
- **`permissions.deny`** uses gitignore-style `Tool(<glob>)` specifiers and is
  evaluated natively.
- **Plugins cannot contribute permissions** — the plugin contribution points are
  skills/commands/agents/hooks/.mcp.json/.lsp.json/monitors/bin/settings(limited).
  So the gated-doc denies must be emitted into the project `.claude/settings.json`.

## Conclusion

The custom allowlist is redundant with native `permissions.deny` (best-effort
prevention) + the integrity stack (executed criteria + human sign-off, ADR-2).
The generation layer already emits the native equivalent, so removing the
allowlist does not leave a capability gap. This concludes the evidence for the
`allowlist-collapse` work item.
