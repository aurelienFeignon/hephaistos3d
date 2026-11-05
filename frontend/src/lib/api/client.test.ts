import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient } from "./client";

describe("typed API client", () => {
  it("uses same-origin URLs, repeated filters, cursors and abort signals without credentials", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ items: [], next_cursor: "next" }));
    const api = createApiClient(fetcher);
    const controller = new AbortController();
    const page = await api.generations({ status: ["FAILED", "CANCELLED"], limit: 25, cursor: "a+b" }, controller.signal);
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/generations?status=FAILED&status=CANCELLED&limit=25&cursor=a%2Bb");
    expect(options).toMatchObject({ cache: "no-store", credentials: "same-origin", signal: controller.signal });
    expect(new Headers(options?.headers).has("authorization")).toBe(false);
    expect(page.next_cursor).toBe("next");
  });

  it("sends FormData without overriding its boundary", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ id: "new" }, { status: 201 }));
    const form = new FormData();
    form.append("image", new Blob(["image"]), "image.png");
    await createApiClient(fetcher).createGeneration(form);
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: "POST", body: form });
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).has("content-type")).toBe(false);
  });

  it("preserves problem+json codes, validation details and HTTP status", async () => {
    const problem = { type: "about:blank", title: "Invalid", status: 422, detail: "Outside bounds", code: "PARAMS_INVALID", errors: [{ loc: ["body", "params", "steps"], msg: "Too large" }] };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(problem, { status: 422, headers: { "Content-Type": "application/problem+json" } }));
    await expect(createApiClient(fetcher).models()).rejects.toMatchObject({ status: 422, code: "PARAMS_INVALID", problem });
  });

  it("does not display HTML or internal error bodies", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("<h1>private path</h1>", { status: 502 }));
    try { await createApiClient(fetcher).models(); expect.fail("should fail"); }
    catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 502, code: "HTTP_ERROR" });
      expect(String(error)).not.toContain("private path");
    }
  });

  it("accepts deletion without a response body", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await createApiClient(fetcher).deleteGeneration("id")).toBeUndefined();
  });

  it("exposes bounded attempts with their next page marker", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json([], { headers: { "X-Next-Attempt": "10" } }));
    expect(await createApiClient(fetcher).attempts("id", { limit: 10, after: 0 })).toEqual({ items: [], next_after: 10 });
  });

  it("preserves cancellation rather than turning it into an API error", async () => {
    const error = new DOMException("Cancelled", "AbortError");
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(error);
    await expect(createApiClient(fetcher).models()).rejects.toBe(error);
  });
});
