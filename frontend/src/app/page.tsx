import { GenerationPanel } from "@/components/studio/GenerationPanel";
import { StudioShell } from "@/components/studio/StudioShell";

// Emplacements neutres : les données réelles arrivent avec P3.5 à P3.8, sans valeur factice d'ici là.
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
      panel={<GenerationPanel />}
      queue={<span className="text-ink-muted">Le suivi des générations s&apos;affichera ici.</span>}
    />
  );
}
