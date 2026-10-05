import { afterEach, describe, expect, it, vi } from "vitest";
import { focusedConversationItems } from "./focused-conversation";
import { HindsightClient } from "./hindsight";
import { retainLiveSession, type TransportTurn } from "./chat";
import { memoryCursorStore } from "./retain-cursor";

const turns: TransportTurn[] = [
  { role: "user", content: "When for Boreal?", timestamp: "2025-05-01T23:59:55Z" },
  { role: "assistant", content: "Tomorrow at 08 UTC.", timestamp: "2025-05-02T00:00:05Z" },
];
const fallback = "2026-10-04T10:00:00Z";
afterEach(() => vi.unstubAllGlobals());

describe("per-message extraction clocks", () => {
  it("freezes clocks and dialogue without using the capture clock for dated messages", () => {
    const source = structuredClone(turns);
    const items = focusedConversationItems(source, fallback);
    source[0].content = "changed";
    expect(items[1].message.timestamp).toBe("2025-05-02T00:00:05.000Z");
    expect(items[1].context_messages[0].content).toBe("When for Boreal?");
    expect(items[1].context_messages[0].timestamp).toBe("2025-05-01T23:59:55.000Z");
  });
  it("uses one fallback for missing/invalid/naive clocks and normalizes offsets", () => {
    const items = focusedConversationItems(
      [
        { role: "user", content: "one" },
        { role: "assistant", content: "two", timestamp: "2025-05-02T00:00:05" },
        { role: "user", content: "three", timestamp: "bad" },
        { role: "assistant", content: "four", timestamp: "2025-05-01T18:00:05-06:00" },
      ],
      fallback
    );
    expect(items.map((item) => item.message.timestamp)).toEqual([
      "2026-10-04T10:00:00.000Z",
      "2026-10-04T10:00:00.000Z",
      "2026-10-04T10:00:00.000Z",
      "2025-05-02T00:00:05.000Z",
    ]);
  });
  it("sends separate API clocks with context inside screened content, preserving document/scopes/provenance", async () => {
    interface WireItem {
      timestamp: string;
      content: string;
      context: string;
      document_id: string;
      strategy?: string;
      update_mode?: string;
      observation_scopes?: string[][];
      metadata?: Record<string, string>;
    }
    const calls: { url: string; body: { operation_id?: string; items: WireItem[] } }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url: String(url), body: JSON.parse(String(init.body)) });
        return new Response(JSON.stringify({ operation_id: "op" }));
      })
    );
    const client = new HindsightClient({
      apiUrl: "http://localhost:8888",
      bank: "test-bank",
      observationScopes: [["daily"]],
    });
    await client.retain(
      "legacy transcript",
      "Authorship policy",
      "conversation:test",
      ["daily"],
      "conversation",
      {
        conversationItems: focusedConversationItems(turns, fallback),
        timestamp: fallback,
        operationId: "stable-id",
        metadata: { harness: "codex" },
        updateMode: "append",
      }
    );
    const body = calls[0].body;
    expect(body.operation_id).toBe("stable-id");
    expect(body.items).toHaveLength(2);
    expect(new Set(body.items.map((item) => item.document_id)).size).toBe(2);
    expect(body.items.map((item: WireItem) => item.timestamp)).toEqual([
      "2025-05-01T23:59:55.000Z",
      "2025-05-02T00:00:05.000Z",
    ]);
    for (const item of body.items) {
      expect(item.strategy).toBeUndefined();
      expect(item.document_id).toMatch(/^conversation:test:message:[0-9a-f]{64}$/);
      expect(item.update_mode).toBeUndefined();
      expect(item.observation_scopes).toEqual([["daily"]]);
      expect(item.metadata).toEqual({ harness: "codex" });
      expect(item.context).not.toContain("When for Boreal?");
    }
    expect(JSON.parse(body.items[1].content).context_messages[0].content).toBe("When for Boreal?");
  });
  it("reuses event IDs across copied continuation files and separates repeated text on different dates", () => {
    const original = focusedConversationItems(turns, fallback);
    const copy = focusedConversationItems(
      [...turns, { ...turns[1], timestamp: "2025-05-03T00:00:05Z" }],
      fallback
    );
    expect(copy[1].documentKey).toBe(original[1].documentKey);
    expect(copy[2].documentKey).not.toBe(original[1].documentKey);
  });
  it("replays frozen contextual items and operation id after a rejected append", async () => {
    const retain = vi.fn().mockResolvedValue(undefined);
    const client = {
      bank: "test-bank",
      retain,
      supportsAppendRetain: async () => true,
    } as unknown as HindsightClient;
    const cursors = memoryCursorStore();
    const write = (source: TransportTurn[]) =>
      retainLiveSession(client, "session", source, fallback, "codex", {
        cursors,
        messageClocks: true,
      });
    await write(turns.slice(0, 1));
    retain.mockRejectedValueOnce(new Error("network"));
    await expect(write(turns)).rejects.toThrow("network");
    const rejected = retain.mock.calls[1][5];
    const pending = JSON.parse(JSON.stringify(cursors.read("session")));
    cursors.write("session", pending); // a fresh hook process reads this from JSON
    expect(pending.pending[0].items[0].context_messages[0].content).toBe("When for Boreal?");
    await write(turns);
    expect(retain.mock.calls[2][5]).toEqual(rejected);
    expect(cursors.read("session")?.pending).toBeUndefined();
    await write(turns);
    expect(retain).toHaveBeenCalledTimes(3);
  });
});
