import { afterEach, describe, expect, it, vi } from "vitest";
import { HindsightClient } from "./hindsight";
afterEach(() => vi.unstubAllGlobals());
describe("bounded memory readiness", () => {
  it.each([{ items: [] }, { items: [{ id: "fact" }] }])(
    "checks memories independently of pages: %j",
    async ({ items }) => {
      const fetch = vi.fn(async (_url: unknown) => new Response(JSON.stringify({ items })));
      vi.stubGlobal("fetch", fetch);
      const client = new HindsightClient({ apiUrl: "http://localhost:8888", bank: "test-bank" });
      expect(await client.hasMemories()).toBe(items.length > 0);
      expect(fetch.mock.calls[0][0]).toContain("/banks/test-bank/memories/list?limit=1");
    }
  );
  it("distinguishes a missing bank from an unsupported readiness endpoint", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Bank test-bank not found" }), { status: 404 })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })
      );
    vi.stubGlobal("fetch", fetch);
    const client = new HindsightClient({ apiUrl: "http://localhost:8888", bank: "test-bank" });
    expect(await client.hasMemories()).toBe(false);
    await expect(client.hasMemories()).rejects.toThrow("endpoint is unavailable");
  });
  it("rejects malformed responses rather than claiming the bank is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}"))
    );
    const client = new HindsightClient({ apiUrl: "http://localhost:8888", bank: "test-bank" });
    await expect(client.hasMemories()).rejects.toThrow("items array");
  });
});
