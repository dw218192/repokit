"""Tests for backend factory and selection logic."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.claude._base import ClaudeBackend
from repo_tools.agent.claude._cli import CliBackend


# ── get_backend tests ────────────────────────────────────────────


class TestGetBackend:
    def test_explicit_cli(self):
        """get_backend('cli') returns CliBackend."""
        from repo_tools.agent.claude import get_backend

        backend = get_backend("cli")
        assert isinstance(backend, CliBackend)

    def test_explicit_sdk(self, monkeypatch):
        """get_backend('sdk') returns SdkBackend."""
        mock_sdk = MagicMock()
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", mock_sdk)

        from repo_tools.agent.claude import get_backend
        from repo_tools.agent.claude._sdk import SdkBackend

        backend = get_backend("sdk")
        assert isinstance(backend, SdkBackend)

    def test_auto_falls_back_to_cli(self, monkeypatch):
        """get_backend(None) falls back to CLI when SDK is not installed."""
        # Ensure claude_agent_sdk is not importable
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)

        from repo_tools.agent.claude import get_backend

        backend = get_backend(None)
        assert isinstance(backend, CliBackend)

    def test_auto_prefers_sdk(self, monkeypatch):
        """get_backend(None) prefers SDK when available."""
        mock_sdk = MagicMock()
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", mock_sdk)

        from repo_tools.agent.claude import get_backend
        from repo_tools.agent.claude._sdk import SdkBackend

        backend = get_backend(None)
        assert isinstance(backend, SdkBackend)

    def test_all_backends_satisfy_protocol(self, monkeypatch):
        """Both backends satisfy ClaudeBackend protocol."""
        mock_sdk = MagicMock()
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", mock_sdk)

        from repo_tools.agent.claude._sdk import SdkBackend

        assert isinstance(CliBackend(), ClaudeBackend)
        assert isinstance(SdkBackend(), ClaudeBackend)


# ── _ensure_backend tests ────────────────────────────────────────


class TestEnsureBackend:
    def test_lazy_init(self, monkeypatch):
        """_ensure_backend creates backend on first call."""
        import repo_tools.agent.tool as tool_mod

        # Reset module-level _backend to None
        monkeypatch.setattr(tool_mod, "_backend", None)

        # Mock get_backend to track calls
        mock_backend = MagicMock()
        with patch("repo_tools.agent.claude.get_backend", return_value=mock_backend) as mock_factory:
            result = tool_mod._ensure_backend({})
            assert result is mock_backend
            mock_factory.assert_called_once_with(None)

    def test_caches_backend(self, monkeypatch):
        """_ensure_backend returns cached backend on subsequent calls."""
        import repo_tools.agent.tool as tool_mod

        mock_backend = MagicMock()
        monkeypatch.setattr(tool_mod, "_backend", mock_backend)

        result = tool_mod._ensure_backend({})
        assert result is mock_backend

    def test_passes_preference(self, monkeypatch):
        """_ensure_backend passes args['backend'] to get_backend."""
        import repo_tools.agent.tool as tool_mod

        monkeypatch.setattr(tool_mod, "_backend", None)

        mock_backend = MagicMock()
        with patch("repo_tools.agent.claude.get_backend", return_value=mock_backend) as mock_factory:
            tool_mod._ensure_backend({"backend": "cli"})
            mock_factory.assert_called_once_with("cli")
