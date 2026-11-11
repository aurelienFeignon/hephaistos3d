import { describe, expect, it } from "vitest";
import { idolCameraPosition, initialView } from "./idolCamera";

const close = (actual: readonly number[], expected: readonly number[]) =>
  actual.forEach((value, index) => expect(value).toBeCloseTo(expected[index], 3));

describe("idolCameraPosition", () => {
  // Positions relevées dans le visualiseur lors de la validation P4.5 (docs/validation/P4.5-camera.md),
  // dont le rendu a été confronté à l'image d'entrée d'Idol.
  it("reproduit les positions validées sur des sorties réelles d'Idol", () => {
    close(idolCameraPosition({ yaw: 0.8740577437001095, pitch: 0.06500103513168896, radius: 5.701990947012135, fov: 18.8 }), [4.364, 0.370, -3.651]);
    close(idolCameraPosition({ yaw: 1.8265495005464096, pitch: 0.7925652531312499, radius: 2.0477307853935707, fov: 55.5 }), [1.391, 1.458, 0.364]);
  });

  it("lit la direction d'Idol (dx, dy, dz) comme (dx, dz, -dy) dans Three.js", () => {
    close(idolCameraPosition({ yaw: 0, pitch: 0, radius: 2, fov: 50 }), [0, 0, -2]);
    close(idolCameraPosition({ yaw: Math.PI / 2, pitch: 0, radius: 2, fov: 50 }), [2, 0, 0]);
    close(idolCameraPosition({ yaw: 0, pitch: Math.PI / 2, radius: 2, fov: 50 }), [0, 2, 0]);
  });
});

describe("initialView", () => {
  const length = ([x, y, z]: readonly number[]) => Math.hypot(x, y, z);
  // Caméra réellement prédite par Idol pour une illustration sans perspective (banc A4.1, 2026-11-11) :
  // à 1000 unités, elle tombait au-delà du plan lointain du visualiseur (100) : écran noir.
  const telephoto = { yaw: 1.1440335196114235, pitch: 0.7890025577800637, radius: 1000, fov: 0.08476380693255726 };

  it("laisse intacte une caméra ordinaire", () => {
    const camera = { yaw: 0.8740577437001095, pitch: 0.06500103513168896, radius: 5.701990947012135, fov: 18.8 };
    expect(initialView(camera)).toEqual({ position: idolCameraPosition(camera), fov: 18.8 });
  });

  it("ramène une quasi-orthographique dans le champ du visualiseur, même direction et même cadrage", () => {
    const view = initialView(telephoto);
    expect(view.fov).toBe(30);
    expect(length(view.position)).toBeLessThan(10);
    // Même direction de vue.
    const raw = idolCameraPosition(telephoto);
    view.position.forEach((value, index) => expect(value / length(view.position)).toBeCloseTo(raw[index] / length(raw), 6));
    // Même demi-largeur vue à l'origine : distance × tan(champ / 2).
    const halfWidth = (distance: number, fov: number) => distance * Math.tan((fov * Math.PI) / 360);
    expect(halfWidth(length(view.position), view.fov)).toBeCloseTo(halfWidth(1000, telephoto.fov), 6);
  });
});
