import { probeFile } from "../probeFile";

export async function GET() {
  return probeFile("camera.json", "application/json");
}
