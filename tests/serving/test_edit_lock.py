"""Edit-lock semantics: single-editor, refresh, release, TTL expiry."""
import importlib

import pytest


@pytest.fixture()
def lock(monkeypatch):
    monkeypatch.setenv("EDIT_LOCK_TTL", "999")
    from aletheia.serving import edit_lock
    importlib.reload(edit_lock)
    yield edit_lock
    edit_lock.release("a@x.com")
    edit_lock.release("b@x.com")


def test_single_editor(lock):
    ok, h = lock.try_acquire("A@x.com")
    assert ok and h.email == "a@x.com"          # normalized lower-case
    ok2, h2 = lock.try_acquire("b@x.com")
    assert not ok2 and h2.email == "a@x.com"    # someone else holds it
    assert lock.holder().email == "a@x.com"


def test_holder_can_refresh(lock):
    lock.try_acquire("a@x.com")
    ok, _ = lock.try_acquire("a@x.com")          # same user re-acquires
    assert ok


def test_release_frees(lock):
    lock.try_acquire("a@x.com")
    lock.release("b@x.com")                       # wrong user → no-op
    assert lock.holder().email == "a@x.com"
    lock.release("a@x.com")
    assert lock.holder() is None
    ok, h = lock.try_acquire("b@x.com")
    assert ok and h.email == "b@x.com"


def test_ttl_expiry(monkeypatch):
    monkeypatch.setenv("EDIT_LOCK_TTL", "0")     # everything is instantly stale
    from aletheia.serving import edit_lock
    importlib.reload(edit_lock)
    edit_lock.try_acquire("a@x.com")
    assert edit_lock.holder() is None            # expired
    ok, h = edit_lock.try_acquire("b@x.com")     # anyone can take it
    assert ok and h.email == "b@x.com"


def test_empty_email_rejected(lock):
    ok, h = lock.try_acquire("")
    assert not ok and h is None
