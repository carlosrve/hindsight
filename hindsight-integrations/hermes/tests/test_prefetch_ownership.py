"""Directed release acceptance with simulated clients; no live API or LLM."""

import threading

import pytest
from conftest import FakeClient


def test_synchronous_recall_queries_the_current_session(provider):
    instance, client = provider({"recall_sync": True}, client=FakeClient(recall_texts=["current memory"]))
    instance.queue_prefetch("old question", session_id="session-1")
    instance.on_session_switch("session-2")
    assert "current memory" in instance.prefetch("current question", session_id="session-2")
    assert [request["query"] for request in client.recalls] == ["current question"]
    instance.shutdown()


@pytest.mark.parametrize("boundary", ["switch", "end", "shutdown", "newer_empty_result"])
def test_late_prefetch_must_not_publish_across_ownership_boundary(provider, monkeypatch, boundary):
    instance, _ = provider({"prefetch_waits_for_retain": False})
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def recall(query):
        if query == "old":
            started.set()
            assert release.wait(5), "test failed to release its synthetic worker"
            finished.set()
            return "stale data", 1
        return "", 0

    monkeypatch.setattr(instance, "_do_recall", recall)
    monkeypatch.setattr(instance, "_join_prefetch", lambda *a, **k: None)
    instance.queue_prefetch("old", session_id="session-1")
    old_worker = instance._prefetch_thread
    try:
        assert started.wait(5)
        if boundary == "switch":
            instance.on_session_switch("session-2")
        elif boundary == "end":
            instance.on_session_end([])
        elif boundary == "shutdown":
            instance.shutdown()
        else:
            instance.queue_prefetch("new", session_id="session-1")
            instance._prefetch_thread.join(5)
        release.set()
        old_worker.join(5)
        assert finished.is_set()
        with instance._prefetch_lock:
            assert instance._prefetch_result == ""
    finally:
        release.set()
        old_worker.join(5)
        instance.shutdown()


def test_superseded_owner_cannot_spawn_prefetch(provider, monkeypatch):
    instance, _ = provider({"prefetch_waits_for_retain": False})
    instance.on_session_switch("session-2")
    monkeypatch.setattr(instance, "_do_recall", lambda q: pytest.fail("stale owner started recall"))
    instance.queue_prefetch("old query", session_id="session-1")
    assert instance._prefetch_thread is None
    instance.shutdown()


def test_shutdown_closes_prefetch_admission_before_flushing(provider, monkeypatch):
    instance, _ = provider({"prefetch_waits_for_retain": False})
    monkeypatch.setattr(instance, "_do_recall", lambda q: pytest.fail("shutdown spawned recall"))
    monkeypatch.setattr(
        instance, "_flush_pending_turns", lambda **kwargs: instance.queue_prefetch("late", session_id="session-1")
    )
    instance.shutdown()
    assert instance._prefetch_thread is None
