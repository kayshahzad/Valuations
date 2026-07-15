"""Offline tests for the LangSmith tracing wiring.

No network, no API key — verifies the degrade-to-no-op contract, the
enable path, idempotency, and the run-labeling config shape. These run
in the default suite.
"""
from __future__ import annotations

import importlib
import os

import pytest


_TRACING_VARS = (
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_PROJECT",
    "LANGCHAIN_PROJECT",
)


@pytest.fixture()
def tracing():
    """Fresh tracing module with all LangSmith env vars cleared.

    ``init_langsmith()`` CREATES several of these vars as a side effect,
    so we can't rely on ``monkeypatch.delenv`` (a no-op for an absent var
    records no restore, letting the created var leak into the rest of the
    pytest session and enable real LangSmith network calls). Instead we
    snapshot every var here and hard-restore it in the finalizer.

    The module caches init state in a module global, so we reload it per
    test to get a clean slate.
    """
    saved = {v: os.environ.get(v) for v in _TRACING_VARS}
    for v in _TRACING_VARS:
        os.environ.pop(v, None)
    import aletheia.eval.tracing as t

    module = importlib.reload(t)
    try:
        yield module
    finally:
        # Restore exactly: delete vars init_langsmith created, put back originals.
        for v in _TRACING_VARS:
            if saved[v] is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = saved[v]
        importlib.reload(t)


def test_noop_without_key(tracing):
    """No key configured → tracing stays off, env not mutated to enable."""
    assert tracing.init_langsmith() is False
    assert tracing.langsmith_enabled() is False
    # Must NOT have flipped tracing on.
    import os

    assert os.environ.get("LANGSMITH_TRACING") in (None, "")
    assert os.environ.get("LANGCHAIN_TRACING_V2") in (None, "")


def test_enables_with_key(tracing, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
    assert tracing.init_langsmith() is True
    assert tracing.langsmith_enabled() is True
    import os

    # Mirrors canonical + legacy names.
    assert os.environ["LANGCHAIN_API_KEY"] == "ls-test-key"
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "aletheia"


def test_legacy_key_name_accepted(tracing, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lc-legacy-key")
    assert tracing.init_langsmith() is True
    assert tracing.langsmith_enabled() is True


def test_explicit_optout_with_key(tracing, monkeypatch):
    """Key present but tracing set falsey → stays off (explicit opt-out)."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert tracing.init_langsmith() is False
    assert tracing.langsmith_enabled() is False


def test_custom_project(tracing, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "aletheia-banks")
    assert tracing.init_langsmith() is True
    import os

    assert os.environ["LANGSMITH_PROJECT"] == "aletheia-banks"


def test_idempotent(tracing, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
    assert tracing.init_langsmith() is True
    # Second call returns cached result without re-evaluating; force re-reads.
    assert tracing.init_langsmith() is True
    # Cached True until forced, even after clearing the env.
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)  # init mirrored the key here
    assert tracing.init_langsmith() is True
    assert tracing.init_langsmith(force=True) is False


def test_trace_config_shape(tracing):
    cfg = tracing.trace_config("JPM")
    assert cfg["run_name"] == "aletheia:JPM"
    assert cfg["metadata"]["ticker"] == "JPM"
    assert cfg["metadata"]["stage"] == "stage4_agents"
    assert "aletheia" in cfg["tags"]
    assert "JPM" in cfg["tags"]


def test_trace_config_extra_metadata_and_tags(tracing):
    cfg = tracing.trace_config("AAPL", metadata={"run_id": "abc"}, tags=["adhoc"])
    assert cfg["metadata"]["run_id"] == "abc"
    assert cfg["metadata"]["ticker"] == "AAPL"
    assert "adhoc" in cfg["tags"]
