"""A lost acceptance reply survives a profile restart without duplicate extraction."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import textwrap
from pathlib import Path

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
        bank_id=bank_id,
        document_id="conversation-outbox",
        retain_async=True,
        items=[module.RetainItem(content="Ada likes tea", timestamp="2025-05-02T00:00:05Z")],
    )
    box.stage(envelope)
    loop = asyncio.get_running_loop()

    def send(packet):
        # The provider's synchronous writer dispatches to its shared client loop.
        # The story does the same with the real SDK on the fixture's event loop.
        return asyncio.run_coroutine_threadsafe(
            client.aretain_batch(
                bank_id=packet.bank_id,
                items=[item.model_dump(mode="json", exclude_none=True) for item in packet.items],
                document_id=packet.document_id,
                retain_async=packet.retain_async,
                operation_id=packet.operation_id,
            ),
            loop,
        ).result(timeout=30)

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


async def test_session_end_flushes_captured_turn_once_through_the_real_api(
    client, llm, bank_id, settled, tmp_path, hindsight_server
):
    llm.on_step("extract_facts", contains='"role": "user"').returns(extracted(fact("Ada likes tea", who="Ada")))
    llm.on_step("extract_facts", contains='"role": "assistant"').returns(
        extracted(fact("The assistant acknowledged Ada's tea preference", who="assistant"))
    )
    llm.on_step("consolidate").returns(consolidation())
    # Simulate only the external Hermes imports in an isolated child; load the
    # actual provider and its lifecycle/writer/outbox, and use the real SDK/API.
    support = Path(__file__).resolve().parents[2] / "hindsight-integrations" / "hermes" / "tests" / "conftest.py"
    code = textwrap.dedent("""
        import importlib.util
        import json
        from pathlib import Path
        import sys
        spec = importlib.util.spec_from_file_location('host_interface', sys.argv[1])
        support = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = support
        spec.loader.exec_module(support)
        plugin = support.plugin
        home = Path(sys.argv[2])
        plugin.get_hermes_home = lambda: home
        plugin._load_config = lambda: {
            'mode': 'cloud', 'api_url': sys.argv[3], 'apiKey': 'test-key',
            'bank_id': sys.argv[4], 'retain_every_n_turns': 3}
        # The simulated host has no credential-persistence/version-probe adapter.
        # Pin the API capability; retain delivery still uses the real SDK/server.
        plugin._check_api_supports_update_mode_append = lambda *args, **kwargs: True
        instance = plugin.HindsightMemoryProvider()
        instance.initialize('source-session')
        sends = []
        deliver = instance._retain_items
        def recorded(items, **kwargs):
            sends.append(items)
            return deliver(items, **kwargs)
        instance._retain_items = recorded
        instance.sync_turn('Ada likes tea', 'Acknowledged')
        assert not sends
        instance.on_session_end([{'role': 'tool', 'content': 'uncaptured tool output'}])
        instance._retain_queue.join()
        assert len(sends) == 1
        instance.on_session_end([])
        instance.on_session_switch('next-session')
        instance.shutdown()
        assert len(sends) == 1
        assert 'uncaptured tool output' not in json.dumps(sends)
        print(json.dumps({'documents': [item['document_id'] for item in sends[0]]}))
    """)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(support),
        str(tmp_path),
        hindsight_server.url,
        bank_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    assert process.returncode == 0, stderr.decode() + stdout.decode()
    report = json.loads(stdout.decode().splitlines()[-1])
    await settled(bank_id)
    recall = await client.arecall(bank_id=bank_id, query="Ada tea")
    assert recall.results
    for document_id in report["documents"]:
        document = await client.documents.get_document(bank_id, document_id)
        assert "uncaptured tool output" not in document.original_text
