import { describe, expect, it, vi } from "vitest";
import { proxyApi, type ProxyConfig } from "./api-proxy";

const token = "proxy-test-token-only-at-least-32-characters";
const config: ProxyConfig = {
  backend: "http://api:8000", token,
  origins: ["http://localhost:3030", "http://127.0.0.1:3030"],
  jsonMaxBytes: 65536, uploadMaxBytes: 1024 * 1024, timeoutMs: 1000,
};
const request = (path = "models", init: RequestInit = {}) => new Request(`http://localhost:3030/api/v1/${path}`, {
  ...init, headers: { Host: "localhost:3030", ...init.headers },
});
const params = (path = "models") => path.split("/");

describe("server API proxy", () => {
  it("injects the server token, strips browser credentials and preserves bounded pagination", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ items: [], next_cursor: "n" }, { headers: { "Set-Cookie": "upstream=private", "X-Next-Attempt": "3" } }));
    const response = await proxyApi(request("queue?limit=2&cursor=x", { headers: { Cookie: "secret=browser", Authorization: "Bearer browser", "X-Forwarded-Host": "evil.example" } }), params("queue"), config, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("http://api:8000/api/v1/queue?limit=2&cursor=x");
    const options = fetcher.mock.calls[0][1]!;
    const headers = new Headers(options.headers);
    expect(headers.get("authorization")).toBe(`Bearer ${token}`);
    expect(headers.has("cookie")).toBe(false);
    expect(headers.has("x-forwarded-host")).toBe(false);
    expect(options.redirect).toBe("manual");
    expect(response.headers.has("set-cookie")).toBe(false);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(response.headers.get("x-next-attempt")).toBe("3");
    expect(await response.text()).not.toContain(token);
  });

  it.each<Record<string, string>>([
    { Host: "evil.example" },
    { Host: "localhost:3030.evil.example" },
    { Origin: "https://evil.example" },
    { Origin: "null" },
    { "Sec-Fetch-Site": "cross-site" },
  ])("rejects an untrusted browser before injecting the token: %j", async (headers) => {
    const fetcher = vi.fn<typeof fetch>();
    const response = await proxyApi(request("system/dispatch", { method: "PUT", body: "{}", headers }), params("system/dispatch"), config, fetcher);
    expect([400, 403]).toContain(response.status);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("requires Origin on mutations, even on the allowed Host", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const response = await proxyApi(request("system/dispatch", { method: "PUT", body: "{}" }), params("system/dispatch"), config, fetcher);
    expect(response.status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards a valid mutation's original Origin and streams its body", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      expect(new Headers(init?.headers).get("origin")).toBe("http://localhost:3030");
      expect(await new Response(init?.body).text()).toBe('{"paused":true}');
      return Response.json({ paused: true });
    });
    expect((await proxyApi(request("system/dispatch", { method: "PUT", body: '{"paused":true}', headers: { Origin: "http://localhost:3030", "Content-Type": "application/json" } }), params("system/dispatch"), config, fetcher)).status).toBe(200);
  });

  it.each([["..", "models"], ["http:", "evil.example"], ["generations", "x/source"], ["docs"], ["openapi.json"], ["models", "%2e%2e"]])("refuses path escapes and non-resource paths: %j", async (...path) => {
    const fetcher = vi.fn<typeof fetch>();
    expect((await proxyApi(request(), path, config, fetcher)).status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects oversized bodies from Content-Length before any upstream call", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const response = await proxyApi(request("system/dispatch", { method: "PUT", body: "{}", headers: { Origin: "http://localhost:3030", "Content-Length": "70000" } }), params("system/dispatch"), config, fetcher);
    expect(response.status).toBe(413);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("bounds chunked bodies without Content-Length", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      await new Response(init?.body).text();
      return Response.json({});
    });
    const response = await proxyApi(request("system/dispatch", { method: "PUT", body: "x".repeat(70000), headers: { Origin: "http://localhost:3030" } }), params("system/dispatch"), config, fetcher);
    expect(response.status).toBe(413);
  });

  it("never follows upstream redirects or exposes their location", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 307, headers: { Location: "https://evil.example" } }));
    const response = await proxyApi(request(), params(), config, fetcher);
    expect(response.status).toBe(502);
    expect(response.headers.has("location")).toBe(false);
  });

  it("passes Range/ETag and partial file streams without buffering", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("ply", { status: 206, headers: { "Content-Type": "application/octet-stream", "Content-Range": "bytes 0-2/20", ETag: '"sha"' } }));
    const path = "artifacts/01M6NY28Y9K6F66N713F8TR1VR/download";
    const response = await proxyApi(request(path, { headers: { Range: "bytes=0-2" } }), params(path), config, fetcher);
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get("range")).toBe("bytes=0-2");
    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 0-2/20");
    expect(await response.text()).toBe("ply");
  });

  it("fails closed without a configured secret and hides upstream errors", async () => {
    const fetcher = vi.fn<typeof fetch>();
    expect((await proxyApi(request(), params(), { ...config, token: "" }, fetcher)).status).toBe(503);
    expect(fetcher).not.toHaveBeenCalled();
    fetcher.mockRejectedValue(new Error(`Internal ${token}`));
    const response = await proxyApi(request(), params(), config, fetcher);
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain(token);
  });
});

it("enforces the upstream deadline", async () => {
  const fetcher = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init!.signal!.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  }));
  const response = await proxyApi(request(), params(), { ...config, timeoutMs: 10 }, fetcher);
  expect(response.status).toBe(504);
  expect(await response.json()).toMatchObject({ code: "UPSTREAM_TIMEOUT" });
});

it("passes cancellation to the backend request", async () => {
  const controller = new AbortController();
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
    controller.abort();
    expect(init!.signal!.aborted).toBe(true);
    throw new Error("aborted");
  });
  const response = await proxyApi(request("models", { signal: controller.signal }), params(), config, fetcher);
  expect(response.status).toBe(408);
});

it.each(["evil.example", "localhost:3030.evil.example"])("rejects an untrusted Host on reads: %s", async (host) => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json([]));
  const response = await proxyApi(request("models", { headers: { Host: host } }), params(), config, fetcher);
  expect(response.status).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});
