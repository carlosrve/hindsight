"""Async dated message documents retain source clocks, background context and replay identity.

These tests assert the public transport contract, not a rigid interpretation of
relative dates. The engine remains responsible for interpreting conversational intent.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import pytest

from pydantic import BaseModel, Field
from hindsight_client_api.exceptions import ApiException
from hindsight_system_tests.payloads import consolidation, extracted, fact

pytestmark = pytest.mark.asyncio


@dataclass
class Message:
    role: str
    content: str
    timestamp: str


@dataclass
class FocusedContent:
    message: Message
    context_messages: list[Message]


class DatedRetainItem(BaseModel):
    content: str
    timestamp: str
    document_id: str
    context: str | None = None
    update_mode: str | None = None
    tags: list[str] = Field(default_factory=list)
    observation_scopes: list[list[str]] = Field(default_factory=list)


async def test_async_message_documents_preserve_clocks_context_and_replay(client, llm, bank_id, settled):
    from datetime import datetime
    import uuid

    llm.on_step("extract_facts", contains='"message": {"role": "user"').returns(
        extracted(fact("The user requested Boreal maintenance", who="User")))
    llm.on_step("extract_facts", contains='"message": {"role": "assistant"').returns(
        extracted(fact("The agent proposed Boreal maintenance", who="Agent")))
    llm.on_step("consolidate").returns(consolidation())
    question = Message("user", "Propose maintenance for Boreal.", "2025-05-01T23:59:55Z")
    answer = Message("assistant", "Tomorrow at 08 UTC.", "2025-05-02T00:00:05Z")
    items = [
        DatedRetainItem(
            content=json.dumps(asdict(FocusedContent(message, context))),
            timestamp=message.timestamp,
            context="Extract only the target message; dated background resolves references and workday intent.",
            document_id=f"conversation-clock:message:{message.role}",
            tags=["daily", "session:clock"],
            observation_scopes=[["daily"]],
        ).model_dump(mode="json", exclude_none=True)
        for message, context in ((question, []), (answer, [question]))
    ]
    operation_id = str(uuid.uuid4())
    await client.aretain_batch(bank_id=bank_id, items=items, retain_async=True, operation_id=operation_id)
    await settled(bank_id)
    for item in items:
        document = await client.documents.get_document(bank_id, item["document_id"])
        assert item["content"] == document.original_text
    recall = await client.arecall(bank_id=bank_id, query="Boreal maintenance", types=["world", "experience"])
    clocks = {result.document_id: datetime.fromisoformat(result.mentioned_at.replace("Z", "+00:00"))
              for result in recall.results}
    for item in items:
        assert clocks[item["document_id"]] == datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
    before = {result.id for result in recall.results}
    await client.aretain_batch(bank_id=bank_id, items=items, retain_async=True, operation_id=operation_id)
    await settled(bank_id)
    replay = await client.arecall(bank_id=bank_id, query="Boreal maintenance", types=["world", "experience"])
    assert {result.id for result in replay.results} == before
    # A later response has its own document and clock; it never appends onto the first message.
    later = DatedRetainItem(content="Later agent confirmation.", timestamp="2025-05-03T10:00:00Z",
                           document_id="conversation-clock:message:later").model_dump(mode="json", exclude_none=True)
    llm.on_step("extract_facts", contains="Later agent confirmation").returns(
        extracted(fact("The agent later confirmed Boreal maintenance", who="Agent")))
    await client.aretain_batch(bank_id=bank_id, items=[later], retain_async=True)
    await settled(bank_id)
    document = await client.documents.get_document(bank_id, items[1]["document_id"])
    assert document.original_text == items[1]["content"]
    recall = await client.arecall(bank_id=bank_id, query="Boreal maintenance", types=["world", "experience"])
    result = next(result for result in recall.results if result.document_id == later["document_id"])
    assert datetime.fromisoformat(result.mentioned_at.replace("Z", "+00:00")) == datetime.fromisoformat(
        later["timestamp"].replace("Z", "+00:00"))
