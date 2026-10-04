"""Per-message extraction items compose with shared documents and later appends.

This experiment distinguishes synchronous composition from the async hook contract. It
asserts source preservation, not the stub model's understanding of relative dates.
The latter is exercised separately with a real-provider synthetic canary.
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


async def test_dated_items_share_a_document_and_append_without_losing_the_question(client, llm, bank_id, settled):
    llm.on_step("extract_facts").returns(extracted(fact("The agent discussed maintenance", who="Agent")))
    llm.on_step("consolidate").returns(consolidation())
    question = Message("user", "Propose maintenance for Boreal.", "2025-05-01T23:59:55Z")
    answer = Message("assistant", "Tomorrow at 08 UTC.", "2025-05-02T00:00:05Z")
    items = [
        DatedRetainItem(
            content=json.dumps(asdict(FocusedContent(message, context))),
            timestamp=message.timestamp,
            context="Extract the target message; dated background only resolves references.",
            document_id="conversation-clock",
            tags=["daily"],
            observation_scopes=[["daily"]],
        ).model_dump(mode="json", exclude_none=True)
        for message, context in ((question, []), (answer, [question]))
    ]
    await client.aretain_batch(bank_id=bank_id, items=items)
    await settled(bank_id)
    document = await client.documents.get_document(bank_id, "conversation-clock")
    assert question.content in document.original_text
    assert answer.content in document.original_text
    assert question.timestamp in document.original_text
    assert answer.timestamp in document.original_text

    followup = Message("user", "Keep it as a proposal, not an execution.", "2025-05-03T10:00:00Z")
    item = DatedRetainItem(
        content=json.dumps(asdict(FocusedContent(followup, [answer]))),
        timestamp=followup.timestamp,
        document_id="conversation-clock",
        update_mode="append",
        tags=["daily"],
        observation_scopes=[["daily"]],
    ).model_dump(mode="json", exclude_none=True)
    await client.aretain_batch(bank_id=bank_id, items=[item])
    await settled(bank_id)
    document = await client.documents.get_document(bank_id, "conversation-clock")
    assert question.content in document.original_text
    assert answer.content in document.original_text
    assert followup.content in document.original_text


async def test_shared_document_message_items_are_rejected_on_the_async_hook_path(client, bank_id):
    """Do not mistake a synchronous preservation test for an async hook fix."""
    question = Message("user", "When is maintenance?", "2025-05-01T23:59:55Z")
    answer = Message("assistant", "Tomorrow at 08 UTC.", "2025-05-02T00:00:05Z")
    items = [
        DatedRetainItem(
            content=json.dumps(asdict(FocusedContent(message, []))),
            timestamp=message.timestamp,
            document_id="conversation-async-clock",
        ).model_dump(mode="json", exclude_none=True)
        for message in (question, answer)
    ]
    with pytest.raises(ApiException) as rejected:
        await client.aretain_batch(bank_id=bank_id, items=items, retain_async=True)
    assert rejected.value.status == 400
    assert "duplicate document_ids" in str(rejected.value.body)
