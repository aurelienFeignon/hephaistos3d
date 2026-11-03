import { probeFile } from "../probeFile";

export async function GET() {
  return probeFile("splat.ply", "application/octet-stream");
}
