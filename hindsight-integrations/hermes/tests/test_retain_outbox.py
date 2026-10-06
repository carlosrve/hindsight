"""Automatic retain failures keep frozen bytes instead of dropping the cleared buffer."""

import json
import os
import stat
from pathlib import Path

import pytest

from conftest import FakeClient, plugin
from hindsight_hermes.retain_outbox import RetainEnvelope, RetainItem, RetainOutbox


class FailingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.fail = True
        self.attempts = []

    async def aretain_batch(self, **kwargs):
        self.attempts.append(kwargs)
        if self.fail:
            raise ConnectionError("temporary outage")
        return await super().aretain_batch(**kwargs)


def test_writer_failure_is_replayed_with_original_bytes_and_operation_id(provider):
    client = FailingClient()
    instance, _ = provider({}, client=client)
    instance.sync_turn("one", "1")
    instance._retain_queue.join()
    assert instance._session_turns == []
    pending = instance._automatic_outbox().pending()
    assert len(pending) == 1
    if os.name != "nt":
        assert stat.S_IMODE(pending[0].stat().st_mode) == 0o600
        assert stat.S_IMODE(pending[0].parent.stat().st_mode) == 0o700
    original = client.attempts[0]
    client.fail = False
    instance.sync_turn("two", "2")
    instance._retain_queue.join()
    assert client.attempts[1] == original
    assert len(client.retains) == 2
    assert instance._automatic_outbox().pending() == []
    instance.shutdown()


def test_pending_handoff_survives_a_new_provider_without_another_turn(provider, monkeypatch):
    old, _ = provider({})
    queued = []
    monkeypatch.setattr(old, "_enqueue_retain", queued.append)
    old.sync_turn("historic message", "historic reply")
    path = old._automatic_outbox().pending()[0]
    envelope = json.loads(path.read_text())
    assert old._session_turns == []
    new, client = provider({})
    new._retain_queue.join()
    assert len(client.retains) == 1
    assert client.retains[0]["operation_id"] == envelope["operation_id"]
    assert client.retains[0]["items"] == envelope["items"]
    assert not path.exists()
    new.shutdown()
    old.shutdown()


def test_shutdown_flushes_below_cadence(provider):
    instance, client = provider({"retain_every_n_turns": 3})
    instance.sync_turn("last question", "last reply")
    assert client.retains == []
    instance.shutdown()
    assert len(client.retains) == 1
    assert "last question" in client.retains[0]["items"][0]["content"]
    assert instance._automatic_outbox().pending() == []


def test_shutdown_outage_keeps_partial_batch_for_restart(provider):
    client = FailingClient()
    instance, _ = provider({"retain_every_n_turns": 3}, client=client)
    instance.sync_turn("last question", "last reply")
    instance.shutdown()
    assert len(instance._automatic_outbox().pending()) == 1


def test_scope_binds_pending_data_to_endpoint_and_bank(tmp_path):
    box = RetainOutbox(tmp_path, mode="cloud", api_url="https://example.test", bank_id="one")
    box.stage(RetainEnvelope(bank_id="one", retain_async=True, items=[RetainItem(content="payload")]))
    for endpoint, bank in [("https://other.test", "one"), ("https://example.test", "two")]:
        other = RetainOutbox(tmp_path, mode="cloud", api_url=endpoint, bank_id=bank)
        assert other.pending() == []
    assert len(box.pending()) == 1


def test_malformed_record_is_not_deleted_or_skipped(tmp_path):
    box = RetainOutbox(tmp_path, mode="cloud", api_url="https://example.test", bank_id="one")
    box.path.mkdir(parents=True)
    path = box.path / "00000000000000000000-bad.json"
    path.write_text("not json")
    sent = []
    with pytest.raises(ValueError):
        box.drain(sent.append)
    assert path.exists()
    assert sent == []


def test_staging_failure_does_not_clear_the_source_buffer(provider, monkeypatch):
    instance, _ = provider({})

    def unavailable(envelope):
        raise OSError("disk unavailable")

    monkeypatch.setattr(instance._automatic_outbox(), "stage", unavailable)
    with pytest.raises(OSError, match="disk unavailable"):
        instance.sync_turn("keep this", "reply")
    assert len(instance._session_turns) == 1
    assert instance._session_turns[0].content_size > 0
    # Avoid a fixture-exit retry of an intentionally broken disk.
    instance._session_turns.clear()
    instance.shutdown()


def test_credentials_do_not_cross_recovery_scopes(tmp_path):
    first = RetainOutbox(tmp_path, mode="cloud", api_url="https://example.test", bank_id="one", auth_identity="first")
    first.stage(RetainEnvelope(bank_id="one", retain_async=True, items=[RetainItem(content="private")]))
    second = RetainOutbox(tmp_path, mode="cloud", api_url="https://example.test", bank_id="one", auth_identity="second")
    assert second.pending() == []
    assert len(first.pending()) == 1


def test_missing_operation_identity_is_kept_instead_of_generating_a_new_id(tmp_path):
    box = RetainOutbox(tmp_path, mode="cloud", api_url="https://example.test", bank_id="one")
    box.stage(RetainEnvelope(bank_id="one", retain_async=True, items=[RetainItem(content="payload")]))
    path = box.pending()[0]
    payload = json.loads(path.read_text())
    del payload["operation_id"]
    path.write_text(json.dumps(payload))
    sent = []
    with pytest.raises(ValueError, match="Invalid retain outbox"):
        box.drain(sent.append)
    assert path.exists()
    assert sent == []


@pytest.mark.parametrize("ending", ["switch", "shutdown"])
@pytest.mark.parametrize("append", [True, False])
def test_session_end_flushes_once_without_ingesting_callback_transcript(provider, monkeypatch, ending, append):
    instance, client = provider({"retain_every_n_turns": 3}, session_id="original-session")
    monkeypatch.setattr(plugin, "_check_api_supports_update_mode_append", lambda *a, **k: append)
    instance.sync_turn("pending question", "pending reply")
    instance.on_session_end([{"role": "tool", "content": "not automatically captured"}])
    instance._retain_queue.join()
    assert len(client.retains) == 1
    sent = client.retains[0]
    assert "pending question" in sent["items"][0]["content"]
    assert "not automatically captured" not in json.dumps(sent)
    assert instance._session_id == "original-session"
    instance.on_session_end([])
    if ending == "switch":
        instance.on_session_switch("next-session")
    instance.shutdown()
    assert client.retains == [sent]


def test_session_end_failure_is_recovered_without_a_new_turn(provider):
    client = FailingClient()
    instance, _ = provider({"retain_every_n_turns": 3}, client=client)
    instance.sync_turn("pending question", "pending reply")
    instance.on_session_end([])
    instance._retain_queue.join()
    original = client.attempts[0]
    assert len(instance._automatic_outbox().pending()) == 1
    instance.shutdown()
    assert len(client.attempts) == 1
    restored, recovered = provider({"retain_every_n_turns": 3})
    restored._retain_queue.join()
    assert recovered.retains == [original]
    assert restored._automatic_outbox().pending() == []
    restored.shutdown()


def test_session_end_staging_failure_preserves_pending_turn(provider, monkeypatch):
    instance, client = provider({"retain_every_n_turns": 3})
    instance.sync_turn("pending question", "pending reply")
    box = instance._automatic_outbox()
    stage = box.stage
    monkeypatch.setattr(instance, "_automatic_outbox", lambda: box)
    monkeypatch.setattr(box, "stage", lambda packet: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError):
        instance.on_session_end([])
    assert len(instance._session_turns) == 1
    monkeypatch.setattr(box, "stage", stage)
    instance.on_session_end([])
    instance._retain_queue.join()
    assert len(client.retains) == 1
    instance.shutdown()


def test_session_end_never_captures_tools_with_auto_retain_disabled(provider):
    instance, client = provider({"auto_retain": False})
    instance.on_session_end([{"role": "user", "content": "uncaptured history"}])
    instance.shutdown()
    assert client.retains == []
