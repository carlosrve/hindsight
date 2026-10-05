"""Profile-local automatic retain envelopes, durable before writer handoff."""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class RetainItem(BaseModel):
    # Extra SDK item fields must round-trip through a restart unchanged.
    model_config = ConfigDict(extra="allow")
    content: str
    timestamp: str | None = None
    context: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    tags: list[str] | None = None
    observation_scopes: list[list[str]] | None = None
    update_mode: str | None = None


class RetainEnvelope(BaseModel):
    version: Literal[1] = 1
    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    bank_id: str
    document_id: str | None = None
    retain_async: bool
    items: list[RetainItem]
    track_ops: bool = True


class RetainOutbox:
    def __init__(self, home: Path, *, mode: str, api_url: str, bank_id: str, auth_identity: str | None = None) -> None:
        # Never replay a profile's pending bytes to another endpoint/bank. Embedded
        # URLs may change port, so bind those to mode/bank rather than a daemon URL.
        endpoint = api_url if mode == "cloud" else mode
        scope = sha256(f"{mode}\n{endpoint}\n{bank_id}\n{auth_identity or ''}".encode()).hexdigest()
        self.bank_id = bank_id
        self.path = home / "hindsight" / "retain-outbox" / scope
        self._lock = Lock()

    def pending(self) -> list[Path]:
        return sorted(self.path.glob("*.json"))

    def stage(self, envelope: RetainEnvelope) -> None:
        if envelope.bank_id != self.bank_id:
            raise ValueError("Cannot stage a retain for another bank")
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = envelope.model_dump_json(exclude_none=True)
        # Temp files have mode 0600. Rename exposes either a complete envelope or
        # none, never a partially written JSON record. fsync before queue handoff.
        fd, temporary = tempfile.mkstemp(prefix=".retain-", dir=self.path)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            destination = self.path / f"{time.time_ns():020d}-{envelope.operation_id}.json"
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    def drain(self, send: Callable[[RetainEnvelope], Any]) -> None:
        # Only writer threads take this lock; no event-loop thread waits on it.
        with self._lock:
            for path in self.pending():
                try:
                    payload = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    continue  # another provider accepted the same idempotent operation
                try:
                    envelope = RetainEnvelope.model_validate_json(payload)
                    if "operation_id" not in envelope.model_fields_set or envelope.bank_id != self.bank_id:
                        raise ValueError("Missing replay identity or mismatched bank")
                except ValueError:
                    # Pydantic validation errors may quote raw message content.
                    raise ValueError(f"Invalid retain outbox envelope: {path.name}; kept for recovery") from None
                send(envelope)  # an error leaves this and later records intact
                path.unlink(missing_ok=True)
