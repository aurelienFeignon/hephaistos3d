import { StudioShell } from "@/components/studio/StudioShell";

// Emplacements de P3.2 : les données réelles arrivent avec P3.3 à P3.8, sans valeur factice d'ici là.
export default function StudioPage() {
  return (
    <StudioShell
      status={<span>État du système non connecté</span>}
      library={<p className="text-ink-muted">Vos générations s&apos;afficheront ici.</p>}
      viewer={
        <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
          <p>Le résultat 3D s&apos;affichera ici.</p>
        </div>
      }
      panel={
        <>
          <p className="text-ink-muted">L&apos;import d&apos;image et les réglages s&apos;afficheront ici.</p>
          <button
            type="button"
            disabled
            className="mt-auto min-h-11 rounded-lg bg-accent px-3 font-medium text-white disabled:opacity-50"
          >
            Générer
          </button>
        </>
      }
      queue={<span className="text-ink-muted">Le suivi des générations s&apos;affichera ici.</span>}
    />
  );
}
