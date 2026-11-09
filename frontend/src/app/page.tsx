import { Studio } from "@/components/studio/Studio";

// Emplacements neutres : les données réelles arrivent avec P3.8 et P4, sans valeur factice d'ici là.
export default function StudioPage() {
  return (
    <Studio
      status={<span>État du système non connecté</span>}
      viewer={
        <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
          <p>Le résultat 3D s&apos;affichera ici.</p>
        </div>
      }
    />
  );
}
