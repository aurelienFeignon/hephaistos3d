import { Studio } from "@/components/studio/Studio";

// Emplacements neutres : les données réelles arrivent avec P3.6 à P3.8, sans valeur factice d'ici là.
export default function StudioPage() {
  return (
    <Studio
      status={<span>État du système non connecté</span>}
      library={<p className="text-ink-muted">Vos générations s&apos;afficheront ici.</p>}
      viewer={
        <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
          <p>Le résultat 3D s&apos;affichera ici.</p>
        </div>
      }
    />
  );
}
