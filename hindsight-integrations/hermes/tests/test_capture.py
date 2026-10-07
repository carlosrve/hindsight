"""Event dates at the actual retain boundary, independent of the writer clock."""

import json
from types import SimpleNamespace

import pytest

import hindsight_hermes as plugin
from hindsight_hermes.capture import normalize_timestamp


@pytest.mark.parametrize(
    "value,expected",
    [
        (1735812000, "2025-01-02T10:00:00Z"),
        (1735812000.125, "2025-01-02T10:00:00.125000Z"),
        ("2025-01-02T04:00:00-06:00", "2025-01-02T10:00:00Z"),
        ("2025-01-02T10:00:00", None),
        (True, None),
        (float("nan"), None),
        (float("inf"), None),
        (10**100, None),
        ("invalid", None),
        (None, None),
    ],
)
def test_timestamp_normalization(value, expected):
    assert normalize_timestamp(value) == expected


def test_buffered_snapshots_keep_event_times_after_delayed_writer(provider, monkeypatch):
    instance, fake = provider({"retain_every_n_turns": 2})
    queued = []
    monkeypatch.setattr(instance, "_enqueue_retain", queued.append)
    monkeypatch.setattr(plugin, "_event_timestamp", lambda: "2026-10-03T10:00:00Z")
    source = [
        {"role": "user", "content": "one", "timestamp": 1735812000},
        {"role": "assistant", "content": "intermediate tool call"},
        {"role": "tool", "content": "must not enter automatic retention"},
        {"role": "assistant", "content": "1", "timestamp": "2025-01-02T10:00:10Z"},
    ]
    instance.sync_turn("one", "1", messages=SimpleNamespace(messages=lambda: source))
    source[0]["timestamp"] = "2030-01-01T00:00:00Z"
    instance.sync_turn(
        "two",
        "2",
        messages=SimpleNamespace(
            messages=lambda: [
                {"role": "user", "content": "two", "timestamp": "2025-02-02T04:00:00-06:00"},
                {"role": "assistant", "content": "2", "timestamp": "2025-02-02T10:00:20Z"},
            ]
        ),
    )
    assert len(queued) == 1
    monkeypatch.setattr(plugin, "_event_timestamp", lambda: "2026-10-04T10:00:00Z")
    queued[0]()
    items = fake.retains[0]["items"]
    assert [item["timestamp"] for item in items] == [
        "2025-01-02T10:00:00Z",
        "2025-01-02T10:00:10Z",
        "2025-02-02T10:00:00Z",
        "2025-02-02T10:00:20Z",
    ]
    assert [json.loads(item["content"])["message"]["timestamp"] for item in items] == [
        item["timestamp"] for item in items
    ]
    assert [json.loads(item["content"])["message"]["content"] for item in items] == [
        "User: one",
        "Assistant: 1",
        "User: two",
        "Assistant: 2",
    ]
    focused = json.loads(items[1]["content"])
    assert focused["context_messages"] == [
        {"role": "user", "content": "User: one", "timestamp": "2025-01-02T10:00:00Z"}
    ]
    assert "must not enter" not in json.dumps(items)
    assert "User: one" not in items[1]["context"]
    assert "document_id" not in fake.retains[0]
    assert fake.retains[0]["retain_async"] is True
    assert len({item["document_id"] for item in items}) == len(items)
    assert all(item["document_id"].startswith("session-1:message:") for item in items)
    assert all("update_mode" not in item for item in items)
    first_ids = [item["document_id"] for item in items]
    queued[0]()
    assert all([item["document_id"] for item in call["items"]] == first_ids for call in fake.retains)
    instance.shutdown()


def test_missing_or_invalid_dates_use_enqueue_time(provider, monkeypatch):
    instance, fake = provider({})
    queued = []
    monkeypatch.setattr(instance, "_enqueue_retain", queued.append)
    monkeypatch.setattr(plugin, "_event_timestamp", lambda: "2026-10-03T10:00:00Z")
    instance.sync_turn(
        "q",
        "a",
        messages=SimpleNamespace(
            messages=lambda: [
                {"role": "user", "content": "q", "timestamp": "2025-01-02T10:00:00"},
                {"role": "assistant", "content": "a"},
            ]
        ),
    )
    monkeypatch.setattr(plugin, "_event_timestamp", lambda: "2026-10-04T10:00:00Z")
    queued[0]()
    assert [item["timestamp"] for item in fake.retains[0]["items"]] == [
        "2026-10-03T10:00:00Z",
        "2026-10-03T10:00:00Z",
    ]
    instance.shutdown()
