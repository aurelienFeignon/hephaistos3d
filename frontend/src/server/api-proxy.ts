import "server-only";

export type ProxyConfig = {
  backend: string;
  token: string;
  origins: string[];
  jsonMaxBytes: number;
  uploadMaxBytes: number;
  timeoutMs: number;
};

function positiveInteger(value: string | undefined, fallback: number): number {
  const parsed = value === undefined ? fallback : Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) throw new Error("Invalid proxy configuration");
  return parsed;
}

export function proxyConfig(): ProxyConfig {
  return {
    backend: process.env.HEPHAISTOS_API_URL ?? "http://127.0.0.1:8000",
    token: process.env.API_ACCESS_TOKEN ?? "",
    origins: (process.env.FRONTEND_ALLOWED_ORIGINS ?? "http://localhost:3030,http://127.0.0.1:3030,http://[::1]:3030").split(",").map((v) => v.trim()),
    jsonMaxBytes: positiveInteger(process.env.API_JSON_MAX_BYTES, 65536),
    uploadMaxBytes: positiveInteger(process.env.UPLOAD_MAX_BYTES, 20971520),
    timeoutMs: positiveInteger(process.env.API_REQUEST_TIMEOUT_SECONDS, 30) * 1000,
  };
}

function problem(status: number, code: string): Response {
  return Response.json({ type: "about:blank", title: "Request rejected", status, detail: code.replaceAll("_", " "), code }, {
    status, headers: { "Content-Type": "application/problem+json", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" },
  });
}

// Fixed API resources only: never forward arbitrary URLs, documentation, or path escapes.
const resourcePath = /^(?:system\/(?:health|status|dispatch)|models(?:\/[A-Za-z0-9_-]+(?:\/(?:schema|presets))?)?|queue|generations(?:\/[0-9A-HJKMNP-TV-Z]{26}(?:\/(?:cancel|retry|source|attempts|logs|artifacts))?)?|artifacts\/[0-9A-HJKMNP-TV-Z]{26}(?:\/download)?)$/;
const requestHeaders = ["content-type", "accept", "range", "if-range", "if-none-match", "if-modified-since", "origin", "sec-fetch-site"];
const responseHeaders = ["content-type", "content-length", "content-disposition", "content-range", "accept-ranges", "etag", "last-modified", "x-next-attempt", "x-request-id"];

export async function proxyApi(request: Request, segments: string[], config: ProxyConfig, fetcher: typeof fetch = fetch): Promise<Response> {
  let backend: URL;
  let origins: URL[];
  try {
    backend = new URL(config.backend);
    origins = config.origins.map((value) => {
      const url = new URL(value);
      if (!["http:", "https:"].includes(url.protocol) || url.origin !== value) throw new Error("Invalid origin");
      return url;
    });
    if (!origins.length || !/^[A-Za-z0-9_-]{32,512}$/.test(config.token) || !["http:", "https:"].includes(backend.protocol) || backend.username || backend.password || backend.pathname !== "/" || backend.search || backend.hash) throw new Error("Invalid backend");
  } catch { return problem(503, "PROXY_NOT_CONFIGURED"); }

  const host = request.headers.get("host")?.toLowerCase();
  if (!host || !origins.some((origin) => origin.host.toLowerCase() === host)) return problem(400, "HOST_NOT_ALLOWED");
  const origin = request.headers.get("origin");
  const mutation = !["GET", "HEAD", "OPTIONS"].includes(request.method);
  if (request.headers.get("sec-fetch-site") === "cross-site" ||
    (origin !== null && !origins.some((allowed) => allowed.origin === origin && allowed.host.toLowerCase() === host)) ||
    (mutation && origin === null)) return problem(403, "ORIGIN_NOT_ALLOWED");

  if (segments.some((part) => !/^[A-Za-z0-9_-]+$/.test(part))) return problem(404, "RESOURCE_NOT_FOUND");
  const path = segments.join("/");
  if (!resourcePath.test(path)) return problem(404, "RESOURCE_NOT_FOUND");
  if (!["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"].includes(request.method)) return problem(405, "METHOD_NOT_ALLOWED");
  const upload = request.method === "POST" && path === "generations";
  const limit = upload ? config.uploadMaxBytes + 256 * 1024 : config.jsonMaxBytes;
  const length = request.headers.get("content-length");
  if (length !== null && (!/^[0-9]+$/.test(length) || !Number.isSafeInteger(Number(length)))) return problem(400, "INVALID_CONTENT_LENGTH");
  if (length !== null && Number(length) > limit) return problem(413, upload ? "UPLOAD_TOO_LARGE" : "REQUEST_TOO_LARGE");

  const headers = new Headers({ Authorization: `Bearer ${config.token}`, "Accept-Encoding": "identity" });
  for (const name of requestHeaders) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  let tooLarge = false;
  let received = 0;
  const body = mutation && request.body ? request.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      received += chunk.byteLength;
      if (received > limit) {
        tooLarge = true;
        controller.error(new Error("Body exceeds limit"));
      } else controller.enqueue(chunk);
    },
  })) : undefined;
  const timeout = AbortSignal.timeout(config.timeoutMs);
  try {
    const init: RequestInit & { duplex?: "half" } = {
      method: request.method, headers, body, ...(body ? { duplex: "half" as const } : {}),
      cache: "no-store", redirect: "manual", signal: AbortSignal.any([request.signal, timeout]),
    };
    const url = `${backend.origin}/api/v1/${path}${new URL(request.url).search}`;
    const upstream = await fetcher(url, init);
    if (upstream.status >= 300 && upstream.status < 400 && upstream.status !== 304) {
      await upstream.body?.cancel();
      return problem(502, "UPSTREAM_REDIRECT_REJECTED");
    }
    const outgoing = new Headers({ "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" });
    for (const name of responseHeaders) {
      const value = upstream.headers.get(name);
      if (value !== null) outgoing.set(name, value);
    }
    const location = upstream.headers.get("location");
    if (location?.startsWith("/api/v1/") && !location.includes("\\")) outgoing.set("location", location);
    return new Response(request.method === "HEAD" ? null : upstream.body, { status: upstream.status, headers: outgoing });
  } catch {
    if (tooLarge) return problem(413, upload ? "UPLOAD_TOO_LARGE" : "REQUEST_TOO_LARGE");
    if (timeout.aborted) return problem(504, "UPSTREAM_TIMEOUT");
    if (request.signal.aborted) return problem(408, "REQUEST_ABORTED");
    return problem(502, "UPSTREAM_UNAVAILABLE");
  }
}
