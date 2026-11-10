// Téléchargement d'un artefact avec progression réelle (P4.2) : Spark charge une URL sans rien
// en dire, on lit donc le flux soi-même et on lui passe les octets.

export type ArtifactErrorKind = "MISSING" | "HTTP" | "NETWORK" | "TRUNCATED" | "INVALID";

export class ArtifactLoadError extends Error {
  constructor(public readonly kind: ArtifactErrorKind, message: string, public readonly status?: number) {
    super(message);
    this.name = "ArtifactLoadError";
  }
}

type Options = {
  signal?: AbortSignal;
  /** Taille connue par l'API : sert de total si l'en-tête manque et détecte un fichier tronqué. */
  expectedBytes?: number;
  onProgress?: (loaded: number, total: number | null) => void;
  fetcher?: typeof fetch;
};

const isAbort = (error: unknown) => error instanceof Error && error.name === "AbortError";

export async function downloadArtifact(url: string, { signal, expectedBytes, onProgress, fetcher = fetch }: Options = {}): Promise<Uint8Array> {
  let response: Response;
  try {
    response = await fetcher(url, { signal, credentials: "same-origin", cache: "no-store" });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ArtifactLoadError("NETWORK", "Le serveur est injoignable.");
  }
  if (response.status === 404 || response.status === 410) {
    await response.body?.cancel();
    throw new ArtifactLoadError("MISSING", "Le fichier est introuvable sur le serveur.", response.status);
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new ArtifactLoadError("HTTP", `Le serveur a refusé le fichier (${response.status}).`, response.status);
  }

  const header = Number(response.headers.get("Content-Length"));
  const total = Number.isSafeInteger(header) && header > 0 ? header : (expectedBytes ?? null);
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  onProgress?.(0, total);
  try {
    const reader = response.body?.getReader();
    while (reader) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      onProgress?.(loaded, total);
    }
  } catch (error) {
    if (isAbort(error) || signal?.aborted) throw error;
    throw new ArtifactLoadError("NETWORK", "Le téléchargement a été interrompu.");
  }
  if (expectedBytes !== undefined && loaded !== expectedBytes) {
    throw new ArtifactLoadError("TRUNCATED", `Fichier incomplet : ${loaded} octets reçus sur ${expectedBytes}.`);
  }

  const bytes = new Uint8Array(loaded);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

/** Signature d'un fichier PLY : `ply` puis une fin de ligne. */
export function isPly(bytes: Uint8Array): boolean {
  if (bytes.length < 4 || bytes[0] !== 0x70 || bytes[1] !== 0x6c || bytes[2] !== 0x79) return false;
  return bytes[3] === 0x0a || (bytes[3] === 0x0d && bytes[4] === 0x0a);
}
