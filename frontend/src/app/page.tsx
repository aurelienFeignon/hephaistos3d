import { Studio } from "@/components/studio/Studio";

// Le visualiseur arrive avec P4 : aucun résultat factice d'ici là.
export default function StudioPage() {
  return (
    <Studio
      viewer={
        <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
          <p>Le résultat 3D s&apos;affichera ici.</p>
        </div>
      }
    />
  );
}
