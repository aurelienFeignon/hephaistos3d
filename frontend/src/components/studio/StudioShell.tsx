"use client";

import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

type SidePanel = "library" | "panel";

type StudioShellProps = {
  status: ReactNode;
  library: ReactNode;
  viewer: ReactNode;
  panel: ReactNode;
  queue: ReactNode;
  panelTitle?: string;
  panelOnMobile?: boolean;
  revealPanelFor?: string | null;
};

const toggleClass =
  "min-h-11 rounded-md border border-line-strong bg-surface px-3 font-medium aria-expanded:border-accent aria-expanded:text-accent";

// Sous lg, un panneau ouvert recouvre la scène pour lui laisser toute la largeur une fois refermé.
const sideClass =
  "absolute inset-y-0 z-10 hidden w-80 max-w-full flex-col gap-3 overflow-y-auto bg-surface p-4 data-[open=true]:flex max-sm:w-full lg:static lg:flex lg:shrink-0";

const headingClass = "text-[13px] font-semibold tracking-[0.06em] text-ink-muted uppercase";

export function StudioShell({
  status,
  library,
  viewer,
  panel,
  queue,
  panelTitle = "Nouvelle génération",
  panelOnMobile = false,
  revealPanelFor = null,
}: StudioShellProps) {
  const [open, setOpen] = useState<SidePanel | null>(null);
  const [revealed, setRevealed] = useState(revealPanelFor);
  if (revealPanelFor !== revealed) {
    setRevealed(revealPanelFor);
    setOpen(revealPanelFor ? "panel" : null);
  }
  const id = useId();
  const libraryId = `${id}-library`;
  const panelId = `${id}-panel`;
  const toggle = (target: SidePanel) => setOpen((current) => (current === target ? null : target));

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-page text-ink">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-line bg-surface px-4 py-2 lg:px-6">
        <span className="flex items-center gap-2.5 text-base font-semibold tracking-[0.01em]">
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-accent"
            aria-hidden="true"
          >
            <path d="M4 18h16" />
            <path d="M7 18v-4h10v4" />
            <path d="M9 14V9l3-5 3 5v5" />
          </svg>
          Hephaistos 3D
        </span>
        <nav aria-label="Navigation principale" className="flex gap-1">
          <Link href="/" aria-current="page" className="flex min-h-11 items-center rounded-md bg-page px-3 font-medium">
            Studio
          </Link>
          <span aria-disabled="true" className="flex min-h-11 items-center px-3 text-ink-faint">
            Historique
          </span>
        </nav>
        <div className="min-w-0 font-mono text-xs text-ink-muted sm:ml-auto">{status}</div>
      </header>

      <div className="flex justify-between gap-2 border-b border-line bg-surface px-4 py-2 lg:hidden">
        <button
          type="button"
          className={toggleClass}
          aria-expanded={open === "library"}
          aria-controls={libraryId}
          onClick={() => toggle("library")}
        >
          Bibliothèque
        </button>
        <button
          type="button"
          className={`${toggleClass} ${panelOnMobile ? "" : "max-sm:hidden"}`}
          aria-expanded={open === "panel"}
          aria-controls={panelId}
          onClick={() => toggle("panel")}
        >
          {panelTitle}
        </button>
      </div>

      <div className="relative flex min-h-0 flex-1">
        <aside
          id={libraryId}
          aria-labelledby={`${libraryId}-title`}
          data-open={open === "library"}
          className={`${sideClass} left-0 border-r border-line lg:w-[280px]`}
        >
          <h2 id={`${libraryId}-title`} className={headingClass}>
            Bibliothèque
          </h2>
          {library}
        </aside>

        <main className="flex min-w-0 flex-1 flex-col p-3 lg:p-4">
          <h1 className="sr-only">Studio</h1>
          <section aria-label="Visualiseur 3D" className="relative min-h-0 flex-1 overflow-hidden rounded-[10px] bg-stage text-stage-ink">
            {viewer}
          </section>
        </main>

        <aside
          id={panelId}
          aria-labelledby={`${panelId}-title`}
          data-open={open === "panel"}
          className={`${sideClass} right-0 border-l border-line lg:w-[340px] ${panelOnMobile ? "" : "max-sm:data-[open=true]:hidden"}`}
        >
          <h2 id={`${panelId}-title`} className={headingClass}>
            {panelTitle}
          </h2>
          {panel}
        </aside>
      </div>

      <section
        aria-label="File d'attente"
        className="flex flex-wrap items-center gap-4 border-t border-line bg-surface px-6 py-2.5 text-[13px] max-sm:hidden"
      >
        <span className="font-semibold text-ink-muted">File</span>
        {queue}
      </section>
    </div>
  );
}
