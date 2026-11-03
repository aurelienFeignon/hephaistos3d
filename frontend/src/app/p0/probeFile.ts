import { readFile } from "node:fs/promises";
import path from "node:path";

// Page de preuve du Gate P0 (G0.7) : sert un résultat de la sonde Idol, sans base ni API.
// Le répertoire vient de l'environnement ; par défaut, var/probe à côté du frontend.
const PROBE_DIR =
  process.env.HEPHAISTOS_PROBE_DIR ??
  path.resolve(process.cwd(), "../var/probe");
export const PROBE_RUN =
  process.env.HEPHAISTOS_PROBE_RUN ?? "nb-armchair-262144-1";

export async function probeFile(
  name: "splat.ply" | "camera.json",
  contentType: string,
): Promise<Response> {
  try {
    const body = await readFile(path.join(PROBE_DIR, PROBE_RUN, name));
    return new Response(body, {
      headers: { "Content-Type": contentType, "Cache-Control": "no-store" },
    });
  } catch {
    return new Response(`${PROBE_RUN}/${name} introuvable dans ${PROBE_DIR}`, {
      status: 404,
    });
  }
}
