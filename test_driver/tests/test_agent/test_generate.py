"""Tests for the standalone generation layer (repo_tools.agent.generate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_tools.agent import generate as gen


@pytest.fixture()
def ctx(tmp_path: Path) -> gen.GenContext:
    project = tmp_path / "proj"
    framework = tmp_path / "fw"
    project.mkdir()
    framework.mkdir()
    return gen.GenContext(
        project_root=project,
        framework_root=framework,
        framework_version="9.9.9",
        config={"agent": {}},
        python_exe="python",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── fresh emission + manifest ────────────────────────────────────────


def test_fresh_generation_writes_surface(ctx: gen.GenContext):
    result = gen.generate(ctx)
    assert result.ok
    assert ".mcp.json" in result.written
    assert ".claude/settings.json" in result.written
    assert (ctx.project_root / ".mcp.json").is_file()
    assert (ctx.project_root / ".claude" / "settings.json").is_file()
    manifest = _read_json(ctx.framework_root / "_managed" / "manifest.json")
    assert manifest["framework_version"] == "9.9.9"
    assert set(manifest["files"]) == {a.target for a in gen.build_artifacts(ctx)}


def test_regeneration_is_idempotent(ctx: gen.GenContext):
    gen.generate(ctx)
    second = gen.generate(ctx)
    assert second.written == []
    assert not second.refused
    # every artifact skipped on the second pass
    assert set(second.skipped) == {a.target for a in gen.build_artifacts(ctx)}


def test_plugin_scaffold_and_spike_skill_emitted(ctx: gen.GenContext):
    gen.generate(ctx)
    manifest = _read_json(ctx.project_root / gen.PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
    assert manifest == {"name": "repokit", "version": "9.9.9"}
    spike = ctx.project_root / gen.PLUGIN_ROOT / "skills" / "spike" / "SKILL.md"
    assert spike.is_file()
    assert "status: concluded" in spike.read_text(encoding="utf-8")


def test_workflow_runner_emitted_beside_plugin(ctx: gen.GenContext):
    gen.generate(ctx)
    runner = ctx.project_root / ".claude" / "workflows" / "repokit-work-item.js"
    assert runner.is_file()
    js = runner.read_text(encoding="utf-8")
    # the runner is project-level, NOT inside the plugin (plugins can't bundle workflows)
    assert gen.PLUGIN_ROOT not in str(runner)
    # gates invoke the personas by the verified NAMESPACED agentType
    assert "repokit:spec-gate" in js
    assert "repokit:impl-gate" in js
    # the human gate is a workflow boundary: returns ready-for-review, doesn't merge
    assert "ready-for-review" in js


def test_reviewer_personas_emitted_as_subagents(ctx: gen.GenContext):
    gen.generate(ctx)
    agents = ctx.project_root / gen.PLUGIN_ROOT / "agents"
    spec = (agents / "spec-gate.md").read_text(encoding="utf-8")
    impl = (agents / "impl-gate.md").read_text(encoding="utf-8")
    # valid subagent frontmatter with a tools line
    assert spec.startswith("---\nname: spec-gate\n")
    assert "tools: Read, Grep, Glob\n" in spec
    # spec gate is read-only (no Bash); impl gate gets Bash to run criteria
    assert "Bash" not in spec.split("---", 2)[1]
    assert "tools: Read, Grep, Glob, Bash\n" in impl
    # adversarial default-fail stance present in the bodies
    assert "default to" in spec.lower() and "fail" in spec.lower()
    assert "re-run" in impl.lower() or "independent re-execution" in impl.lower()


# ── .mcp.json content: survivors only ────────────────────────────────


def test_mcp_has_survivors_not_driver_servers(ctx: gen.GenContext):
    gen.generate(ctx)
    servers = _read_json(ctx.project_root / ".mcp.json")["mcpServers"]
    assert "lint" in servers
    for dead in ("coderabbit", "dispatch", "tickets"):
        assert dead not in servers


def test_mcp_in_file_merge_preserves_foreign_and_repokit_wins(ctx: gen.GenContext):
    target = ctx.project_root / ".mcp.json"
    target.write_text(json.dumps({"mcpServers": {
        "custom": {"type": "stdio", "command": "x"},
        "lint": {"type": "stdio", "command": "STALE"},
    }}), encoding="utf-8")
    gen.generate(ctx)
    servers = _read_json(target)["mcpServers"]
    assert servers["custom"] == {"type": "stdio", "command": "x"}   # foreign preserved
    assert servers["lint"]["command"] == "python"                    # repokit wins


# ── .claude/settings.json content ────────────────────────────────────


def test_settings_has_denies_and_adr_hook(ctx: gen.GenContext):
    gen.generate(ctx)
    settings = _read_json(ctx.project_root / ".claude" / "settings.json")
    deny = settings["permissions"]["deny"]
    assert "Edit(docs/adr/**)" in deny
    assert "Write(_managed/**)" in deny
    hook_cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "adr_immutable" in hook_cmd


# ── adoption guard ───────────────────────────────────────────────────


def test_adoption_guard_refuses_unmanaged_settings(ctx: gen.GenContext):
    settings_path = ctx.project_root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('{"mine": true}', encoding="utf-8")
    result = gen.generate(ctx)
    refused_targets = [t for t, _ in result.refused]
    assert ".claude/settings.json" in refused_targets
    assert not result.ok
    # untouched
    assert _read_json(settings_path) == {"mine": True}


# ── staleness triggers ───────────────────────────────────────────────


def test_hand_edit_is_regenerated(ctx: gen.GenContext):
    gen.generate(ctx)
    settings_path = ctx.project_root / ".claude" / "settings.json"
    settings_path.write_text('{"tampered": true}', encoding="utf-8")
    result = gen.generate(ctx)
    assert ".claude/settings.json" in result.written
    assert "tampered" not in settings_path.read_text(encoding="utf-8")


def test_framework_version_bump_regenerates(ctx: gen.GenContext):
    gen.generate(ctx)
    # Simulate a framework bump: same content, newer version → regen (GEN-2).
    ctx.framework_version = "9.9.10"
    result = gen.generate(ctx)
    assert set(result.written) == {a.target for a in gen.build_artifacts(ctx)}
    manifest = _read_json(ctx.framework_root / "_managed" / "manifest.json")
    assert manifest["framework_version"] == "9.9.10"
