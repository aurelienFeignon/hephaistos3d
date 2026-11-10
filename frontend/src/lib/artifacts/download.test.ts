import { describe, expect, it, vi } from "vitest";
import { ArtifactLoadError, downloadArtifact, isPly } from "./download";

const PLY = new TextEncoder().encode("ply\nformat binary_little_endian 1.0\nend_header\n0123456789");

function streamOf(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

const respond = (body: BodyInit | null, init: ResponseInit = {}) => vi.fn(async () => new Response(body, init));

describe("downloadArtifact", () => {
  it("rend les octets et signale une progression croissante jusqu'au total", async () => {
    const half = PLY.length >> 1;
    const fetcher = respond(streamOf([PLY.slice(0, half), PLY.slice(half)]), { headers: { "Content-Length": String(PLY.length) } });
    const progress: Array<[number, number | null]> = [];
    const bytes = await downloadArtifact("/f", { fetcher, onProgress: (loaded, total) => progress.push([loaded, total]) });
    expect(Array.from(bytes)).toEqual(Array.from(PLY));
    expect(progress.at(-1)).toEqual([PLY.length, PLY.length]);
    expect(progress.map(([loaded]) => loaded)).toEqual([...progress.map(([loaded]) => loaded)].sort((a, b) => a - b));
    expect(progress.length).toBeGreaterThanOrEqual(2);
  });

  it("prend la taille attendue quand l'en-tête manque, et la laisse inconnue sinon", async () => {
    const totals = new Set<number | null>();
    await downloadArtifact("/f", { fetcher: respond(streamOf([PLY])), expectedBytes: PLY.length, onProgress: (_, total) => totals.add(total) });
    expect([...totals]).toEqual([PLY.length]);
    totals.clear();
    await downloadArtifact("/f", { fetcher: respond(streamOf([PLY])), onProgress: (_, total) => totals.add(total) });
    expect([...totals]).toEqual([null]);
  });

  it("qualifie un fichier absent côté serveur", async () => {
    const fetcher = respond(JSON.stringify({ code: "ARTIFACT_NOT_FOUND" }), { status: 404 });
    await expect(downloadArtifact("/f", { fetcher })).rejects.toMatchObject({ kind: "MISSING" });
  });

  it("qualifie un autre refus HTTP et une coupure réseau", async () => {
    await expect(downloadArtifact("/f", { fetcher: respond("", { status: 502 }) })).rejects.toMatchObject({ kind: "HTTP", status: 502 });
    const broken = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    await expect(downloadArtifact("/f", { fetcher: broken })).rejects.toMatchObject({ kind: "NETWORK" });
  });

  it("refuse un fichier tronqué par rapport à la taille attendue", async () => {
    const fetcher = respond(streamOf([PLY.slice(0, 10)]));
    const error = await downloadArtifact("/f", { fetcher, expectedBytes: PLY.length }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ArtifactLoadError);
    expect(error).toMatchObject({ kind: "TRUNCATED" });
  });

  it("transmet l'annulation sans la déguiser en erreur de fichier", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetcher = vi.fn(async (_: RequestInfo | URL, init?: RequestInit) => {
      init?.signal?.throwIfAborted();
      return new Response(PLY);
    });
    const error = await downloadArtifact("/f", { fetcher, signal: controller.signal }).catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(ArtifactLoadError);
    expect((error as Error).name).toBe("AbortError");
  });
});

describe("isPly", () => {
  it("reconnaît la signature PLY, fins de ligne Unix ou Windows", () => {
    expect(isPly(PLY)).toBe(true);
    expect(isPly(new TextEncoder().encode("ply\r\nformat ascii 1.0"))).toBe(true);
  });

  it("refuse un autre contenu, y compris une page d'erreur", () => {
    expect(isPly(new TextEncoder().encode("<!DOCTYPE html>"))).toBe(false);
    expect(isPly(new TextEncoder().encode("plyx\n"))).toBe(false);
    expect(isPly(new Uint8Array())).toBe(false);
  });
});
