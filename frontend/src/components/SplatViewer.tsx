"use client";

import { SparkRenderer, SplatFileType, SplatMesh } from "@sparkjsdev/spark";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { initialView, type IdolCamera } from "@/lib/viewer/idolCamera";

export type { IdolCamera };

const BACKGROUNDS = { dark: 0x15171a, light: 0xe9e6e0 } as const;

// Classes Tailwind : la règle Turbopack de next.config.ts fait passer tout *.css par le chargeur
// Tailwind, ce qui vide les CSS modules (styles.x vaut undefined) — mesuré le 2026-11-03.
const BUTTON =
  "flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg text-[#e8e6e1] hover:bg-[#23262b] " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[#f2a56e] " +
  "aria-pressed:bg-[#2e3137] aria-pressed:text-[#f2a56e]";
const ICON =
  "h-[18px] w-[18px] fill-none stroke-current stroke-2 [stroke-linecap:round] [stroke-linejoin:round]";

/** Échec propre à la scène : pas de contexte WebGL, ou Spark ne sait pas décoder le fichier. */
export type SceneError = "WEBGL" | "DECODE";

type Props = {
  /** PLY déjà téléchargé : la progression et les erreurs de transfert sont gérées en amont (P4.2). */
  bytes: Uint8Array;
  camera: IdolCamera | null;
  label: string;
  onError: (kind: SceneError) => void;
};

// Position de la caméra arrondie, exposée en attribut pour vérifier rotation et réinitialisation (A4.2).
const cameraState = (camera: THREE.PerspectiveCamera) =>
  [...camera.position.toArray(), camera.fov].map((value) => value.toFixed(3)).join(",");

export function SplatViewer({ bytes, camera: idolCamera, label, onError }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);
  const resetRef = useRef<() => void>(() => {});
  const controlsRef = useRef<OrbitControls | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const [status, setStatus] = useState("Chargement…");
  const [fps, setFps] = useState(0);
  const [splats, setSplats] = useState<number | null>(null);
  const [autoRotate, setAutoRotate] = useState(false);
  const [dark, setDark] = useState(true);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let disposed = false;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: false,
        preserveDrawingBuffer: true,
      });
    } catch {
      onErrorRef.current("WEBGL");
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.prepend(renderer.domElement);
    renderer.domElement.className =
      "absolute inset-0 block h-full w-full touch-none";

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BACKGROUNDS.dark);
    sceneRef.current = scene;
    const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100);
    const spark = new SparkRenderer({ renderer });
    scene.add(spark);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controlsRef.current = controls;
    const publishCamera = () => {
      container.dataset.camera = cameraState(camera);
    };
    controls.addEventListener("change", publishCamera);

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = container;
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();

    // Copie : Spark peut céder le tampon à son worker, et l'effet peut être rejoué avec les mêmes octets.
    const mesh = new SplatMesh({ fileBytes: bytes.slice(), fileType: SplatFileType.PLY });
    mesh.rotation.x = Math.PI; // splats en y vers le bas (pieds du fauteuil en +y) ; Three.js : y vers le haut
    scene.add(mesh);

    const view = idolCamera ? initialView(idolCamera) : null;
    const home = view
      ? { position: new THREE.Vector3(...view.position), fov: view.fov }
      : { position: new THREE.Vector3(0, 0, 3), fov: 50 };
    resetRef.current = () => {
      // `controls.reset()` garde l'élan de l'amortissement : la vue repartirait après coup.
      // Une mise à jour sans amortissement consomme cet élan avant de replacer la caméra.
      controls.enableDamping = false;
      controls.update();
      camera.position.copy(home.position);
      camera.fov = home.fov;
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.update();
      controls.enableDamping = true;
      publishCamera();
    };
    resetRef.current();

    mesh.initialized
      .then(() => {
        if (disposed) return;
        setSplats(mesh.packedSplats?.numSplats ?? null);
        setStatus("");
        container.dataset.ready = "true";
      })
      .catch(() => {
        if (!disposed) onErrorRef.current("DECODE");
      });

    let frames = 0;
    let since = performance.now();
    renderer.setAnimationLoop(() => {
      controls.update();
      renderer.render(scene, camera);
      frames += 1;
      const now = performance.now();
      if (now - since >= 1000) {
        setFps(Math.round((frames * 1000) / (now - since)));
        frames = 0;
        since = now;
      }
    });

    return () => {
      disposed = true;
      renderer.setAnimationLoop(null);
      observer.disconnect();
      controls.removeEventListener("change", publishCamera);
      controls.dispose();
      mesh.dispose();
      spark.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [bytes, idolCamera]);

  useEffect(() => {
    if (controlsRef.current) controlsRef.current.autoRotate = autoRotate;
  }, [autoRotate]);

  useEffect(() => {
    if (sceneRef.current)
      sceneRef.current.background = new THREE.Color(
        dark ? BACKGROUNDS.dark : BACKGROUNDS.light,
      );
  }, [dark]);

  return (
    <div
      ref={containerRef}
      data-testid="splat-viewer"
      className={`absolute inset-0 overflow-hidden ${dark ? "bg-[#15171a] text-[#c9c6bf]" : "bg-[#e9e6e0] text-[#3f4248]"}`}
    >
      <div className="pointer-events-none absolute top-4 left-4 flex flex-col gap-1 font-mono text-[11px]">
        <span>{label}</span>
        <span>
          {fps} ips · {splats === null ? "…" : splats.toLocaleString("fr-FR")}{" "}
          gaussiennes
        </span>
      </div>
      {status && (
        <p
          className="pointer-events-none absolute inset-0 m-0 flex items-center justify-center text-sm"
          role="status"
        >
          {status}
        </p>
      )}
      <div
        role="toolbar"
        aria-label="Contrôles de la vue"
        className="absolute bottom-4 left-1/2 flex -translate-x-1/2 gap-1 rounded-[10px] border border-[#2e3137] bg-[rgba(21,23,26,0.86)] p-1"
      >
        <button
          type="button"
          className={BUTTON}
          aria-label="Réinitialiser la vue"
          onClick={() => resetRef.current()}
        >
          <svg viewBox="0 0 24 24" className={ICON} aria-hidden="true">
            <path d="M3 12a9 9 0 1 0 3-6.7" />
            <path d="M3 4v5h5" />
          </svg>
        </button>
        <button
          type="button"
          className={BUTTON}
          aria-label="Rotation automatique"
          aria-pressed={autoRotate}
          onClick={() => setAutoRotate((v) => !v)}
        >
          <svg viewBox="0 0 24 24" className={ICON} aria-hidden="true">
            <ellipse cx="12" cy="12" rx="9" ry="4" />
            <path d="M17 8.5l2.5-1-0.5 2.6" />
          </svg>
        </button>
        <button
          type="button"
          className={BUTTON}
          aria-label="Fond clair ou sombre"
          aria-pressed={!dark}
          onClick={() => setDark((v) => !v)}
        >
          <svg viewBox="0 0 24 24" className={ICON} aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 3v18" />
          </svg>
        </button>
        <span className="mx-0.5 my-2 w-px bg-[#2e3137]" />
        <button
          type="button"
          className={BUTTON}
          aria-label="Plein écran"
          onClick={() => void containerRef.current?.requestFullscreen()}
        >
          <svg viewBox="0 0 24 24" className={ICON} aria-hidden="true">
            <path d="M4 9V4h5" />
            <path d="M20 9V4h-5" />
            <path d="M4 15v5h5" />
            <path d="M20 15v5h-5" />
          </svg>
        </button>
      </div>
      <p className="pointer-events-none absolute right-4 bottom-4 m-0 hidden text-right text-xs leading-relaxed sm:block">
        Glisser : rotation
        <br />
        Molette : zoom
        <br />
        Clic droit : déplacement
      </p>
    </div>
  );
}
