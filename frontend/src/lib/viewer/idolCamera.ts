/** Caméra prédite par Idol pour l'image d'entrée (`camera.json`) : lacet et tangage en radians, champ en degrés. */
export type IdolCamera = {
  yaw: number;
  pitch: number;
  radius: number;
  fov: number;
};

// Repères établis le 2026-11-03 (G0.7), par la géométrie puis par comparaison avec l'image d'entrée :
// les splats ont y vers le BAS, d'où le demi-tour autour de x ; la direction de caméra d'Idol
// (camera.py : yaw = atan2(dx, dy), pitch = asin(dz)) se lit dans Three.js comme (dx, dz, -dy).
// Des huit conventions essayées, c'est la seule qui reproduit le point de vue de l'image d'entrée.
// Confirmé sur trois objets le 2026-11-11 (P4.5), contre deux conventions concurrentes :
// docs/validation/P4.5-camera.md. Ne pas modifier sans refaire cette comparaison.
/** Position de la caméra dans le repère Three.js de la scène, en [x, y, z]. */
export function idolCameraPosition({ yaw, pitch, radius }: IdolCamera): [number, number, number] {
  const dx = Math.cos(pitch) * Math.sin(yaw);
  const dy = Math.cos(pitch) * Math.cos(yaw);
  const dz = Math.sin(pitch);
  return [dx * radius, dz * radius, -dy * radius];
}

/** En deçà de ce champ (degrés), la caméra d'Idol est traitée comme une vue quasi orthographique. */
const MIN_FOV = 10;
/** Champ de vue employé pour rendre une telle vue. */
const ORTHOGRAPHIC_FALLBACK_FOV = 30;

/**
 * Vue initiale du visualiseur. Sur une illustration sans perspective, Idol peut prédire une caméra
 * très lointaine à champ quasi nul (mesuré : 1000 unités, 0,08°), hors du plan lointain du visualiseur.
 * On garde alors la direction et le cadrage, avec un champ ordinaire et la distance qui montre la même
 * largeur à l'origine.
 */
export function initialView(camera: IdolCamera): { position: [number, number, number]; fov: number } {
  if (camera.fov >= MIN_FOV) return { position: idolCameraPosition(camera), fov: camera.fov };
  const halfWidth = camera.radius * Math.tan((camera.fov * Math.PI) / 360);
  const radius = halfWidth / Math.tan((ORTHOGRAPHIC_FALLBACK_FOV * Math.PI) / 360);
  return { position: idolCameraPosition({ ...camera, radius }), fov: ORTHOGRAPHIC_FALLBACK_FOV };
}
