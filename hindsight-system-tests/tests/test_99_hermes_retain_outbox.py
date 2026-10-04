"""A lost acceptance reply survives a profile restart without duplicate extraction."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest

from hindsight_system_tests.payloads import consolidation, extracted, fact

pytestmark = pytest.mark.asyncio


def _outbox_module():
    source = Path(__file__).resolve().parents[2] / "hindsight-integrations" / "hermes" / "retain_outbox.py"
    spec = importlib.util.spec_from_file_location("system_hermes_outbox", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def test_lost_async_reply_replays_the_frozen_operation_without_new_facts(client, llm, bank_id, settled, tmp_path):
    module = _outbox_module()
    llm.on_step("extract_facts").returns(extracted(fact("Ada likes tea", who="Ada")))
    llm.on_step("consolidate").returns(consolidation())
    box = module.RetainOutbox(tmp_path, mode="cloud", api_url="test-endpoint", bank_id=bank_id)
    envelope = module.RetainEnvelope(
        bank_id=bank_id, document_id="conversation-outbox", retain_async=True,
        items=[module.RetainItem(content="Ada likes tea", timestamp="2025-05-02T00:00:05Z")],
    )
    box.stage(envelope)
    loop = asyncio.get_running_loop()

    def send(packet):
        # The provider's synchronous writer dispatches to its shared client loop.
        # The story does the same with the real SDK on the fixture's event loop.
        return asyncio.run_coroutine_threadsafe(client.aretain_batch(
            bank_id=packet.bank_id, items=[item.model_dump(mode="json", exclude_none=True) for item in packet.items],
            document_id=packet.document_id, retain_async=packet.retain_async, operation_id=packet.operation_id,
        ), loop).result(timeout=30)

    def lost_reply(packet):
        send(packet)
        raise ConnectionError("acceptance reply was lost")

    with pytest.raises(ConnectionError):
        await asyncio.to_thread(box.drain, lost_reply)
    assert len(box.pending()) == 1
    await settled(bank_id)
    before = await client.arecall(bank_id=bank_id, query="Ada tea")
    recovered = module.RetainOutbox(tmp_path, mode="cloud", api_url="test-endpoint", bank_id=bank_id)
    await asyncio.to_thread(recovered.drain, send)
    await settled(bank_id)
    after = await client.arecall(bank_id=bank_id, query="Ada tea")
    assert recovered.pending() == []
    assert {result.id for result in before.results} == {result.id for result in after.results}
    assert before.results  # equality must not pass on two empty recalls
    document = await client.documents.get_document(bank_id, "conversation-outbox")
    assert document.original_text == "Ada likes tea"
