import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Generation = Schemas["GenerationView"];
export type Model = Schemas["ModelView"];
export type QueueView = Schemas["QueueView"];
export type QueueItem = Schemas["QueueItem"];
export type Problem = paths["/api/v1/models"]["get"]["responses"][400]["content"]["application/problem+json"];
export type GenerationQuery = NonNullable<paths["/api/v1/generations"]["get"]["parameters"]["query"]>;
type DispatchUpdate = Schemas["DispatchUpdate"];
type Query = Record<string, string | number | boolean | readonly string[] | null | undefined>;

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, public readonly problem?: Problem) {
    super(problem?.detail ?? `La requête a échoué (${status}).`);
    this.name = "ApiError";
  }
}

function isProblem(value: unknown): value is Problem {
  if (typeof value !== "object" || value === null) return false;
  const p = value as Record<string, unknown>;
  return typeof p.type === "string" && typeof p.title === "string" && typeof p.status === "number" && typeof p.detail === "string" && typeof p.code === "string";
}

export function createApiClient(fetcher: typeof fetch = fetch) {
  async function request<T>(path: string, init: RequestInit = {}, query?: Query): Promise<{ data: T; response: Response }> {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value === undefined || value === null) continue;
      for (const item of Array.isArray(value) ? value : [value]) search.append(key, String(item));
    }
    const url = `/api/v1${path}${search.size ? `?${search}` : ""}`;
    const response = await fetcher(url, { ...init, credentials: "same-origin", cache: "no-store" });
    if (!response.ok) {
      let body: unknown;
      try { body = await response.json(); } catch { /* Non-JSON proxy errors remain generic. */ }
      throw new ApiError(response.status, isProblem(body) ? body.code : "HTTP_ERROR", isProblem(body) ? body : undefined);
    }
    if (response.status === 204) return { data: undefined as T, response };
    try { return { data: await response.json() as T, response }; }
    catch { throw new ApiError(response.status, "INVALID_RESPONSE"); }
  }
  async function get<T>(path: string, query?: Query, signal?: AbortSignal) {
    return (await request<T>(path, { signal }, query)).data;
  }
  async function json<T>(path: string, method: string, body?: unknown, signal?: AbortSignal) {
    return (await request<T>(path, { method, signal, ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }) })).data;
  }
  const generationPath = (id: string) => `/generations/${encodeURIComponent(id)}`;
  return {
    health: (signal?: AbortSignal) => get<Schemas["Health"]>("/system/health", undefined, signal),
    status: (signal?: AbortSignal) => get<Schemas["SystemStatus"]>("/system/status", undefined, signal),
    dispatch: (signal?: AbortSignal) => get<Schemas["DispatchState"]>("/system/dispatch", undefined, signal),
    setDispatch: (body: DispatchUpdate, signal?: AbortSignal) => json<Schemas["DispatchState"]>("/system/dispatch", "PUT", body, signal),
    models: (signal?: AbortSignal) => get<Model[]>("/models", undefined, signal),
    model: (id: string, signal?: AbortSignal) => get<Model>(`/models/${encodeURIComponent(id)}`, undefined, signal),
    modelSchema: (id: string, signal?: AbortSignal) => get<Record<string, unknown>>(`/models/${encodeURIComponent(id)}/schema`, undefined, signal),
    presets: (id: string, signal?: AbortSignal) => get<Record<string, Schemas["PresetView"]>>(`/models/${encodeURIComponent(id)}/presets`, undefined, signal),
    generations: (query?: GenerationQuery, signal?: AbortSignal) => get<Schemas["GenerationPage"]>("/generations", query, signal),
    generation: (id: string, signal?: AbortSignal) => get<Generation>(generationPath(id), undefined, signal),
    queue: (query?: { limit?: number; cursor?: string }, signal?: AbortSignal) => get<Schemas["QueueView"]>("/queue", query, signal),
    createGeneration: async (body: FormData, signal?: AbortSignal) => (await request<Generation>("/generations", { method: "POST", body, signal })).data,
    cancel: (id: string, signal?: AbortSignal) => json<Generation>(`${generationPath(id)}/cancel`, "POST", undefined, signal),
    retry: (id: string, signal?: AbortSignal) => json<Generation>(`${generationPath(id)}/retry`, "POST", undefined, signal),
    protect: (id: string, protectedValue: boolean, signal?: AbortSignal) => json<Generation>(generationPath(id), "PATCH", { protected: protectedValue }, signal),
    deleteGeneration: (id: string, signal?: AbortSignal) => json<void>(generationPath(id), "DELETE", undefined, signal),
    attempts: async (id: string, query?: { limit?: number; after?: number }, signal?: AbortSignal) => {
      const { data, response } = await request<Schemas["AttemptView"][]>(`${generationPath(id)}/attempts`, { signal }, query);
      const next = response.headers.get("X-Next-Attempt");
      return { items: data, next_after: next !== null && /^[1-9][0-9]*$/.test(next) ? Number(next) : null };
    },
    logs: (id: string, query: { attempt: number; tail?: number }, signal?: AbortSignal) => get<Record<string, unknown>[]>(`${generationPath(id)}/logs`, query, signal),
    artifacts: (id: string, signal?: AbortSignal) => get<Schemas["ArtifactView"][]>(`${generationPath(id)}/artifacts`, undefined, signal),
    sourceUrl: (id: string, original = false) => `/api/v1${generationPath(id)}/source${original ? "?original=true" : ""}`,
    downloadUrl: (id: string) => `/api/v1/artifacts/${encodeURIComponent(id)}/download`,
  };
}
