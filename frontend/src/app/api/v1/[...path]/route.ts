import { proxyApi, proxyConfig } from "../../../../server/api-proxy";

type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, context: Context): Promise<Response> {
  const { path } = await context.params;
  try {
    return await proxyApi(request, path, proxyConfig());
  } catch {
    return Response.json({ type: "about:blank", title: "Service unavailable", status: 503, detail: "Proxy configuration unavailable", code: "PROXY_NOT_CONFIGURED" }, {
      status: 503, headers: { "Content-Type": "application/problem+json", "Cache-Control": "no-store" },
    });
  }
}

export { handle as GET, handle as HEAD, handle as POST, handle as PUT, handle as PATCH, handle as DELETE, handle as OPTIONS };
