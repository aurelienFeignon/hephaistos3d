"use client";

import Image from "next/image";
import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  ACCEPTED_MIME_TYPES,
  decodeInBrowser,
  inspectImage,
  UPLOAD_MAX_BYTES,
  type ImageDecoder,
  type InspectedImage,
} from "@/lib/image/inspect";

const MIB = 1024 * 1024;
const oneDecimal = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function formatSize(bytes: number) {
  return bytes < MIB ? `${Math.ceil(bytes / 1024)} Ko` : `${oneDecimal.format(bytes / MIB)} Mo`;
}

export function ImagePicker({ decode = decodeInBrowser }: { decode?: ImageDecoder }) {
  const [image, setImage] = useState<InspectedImage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);
  // Seule la lecture la plus récente a le droit d'afficher son résultat.
  const latestRead = useRef(0);

  useEffect(() => () => image?.release(), [image]);
  useEffect(
    () => () => {
      latestRead.current += 1;
    },
    [],
  );

  function reset() {
    latestRead.current += 1;
    setImage(null);
    setError(null);
    setReading(false);
  }

  async function pick(file: File) {
    reset();
    const read = latestRead.current;
    setReading(true);
    const result = await inspectImage(file, decode);
    if (read !== latestRead.current) {
      if (result.ok) result.image.release();
      return;
    }
    setReading(false);
    if (result.ok) setImage(result.image);
    else setError(result.error);
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const { files } = event.dataTransfer;
    if (files.length === 1) void pick(files[0]);
    else {
      reset();
      setError("Déposez une seule image à la fois.");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {image && (
        <figure className="flex flex-col gap-2">
          <div className="flex h-48 items-center justify-center overflow-hidden rounded-md bg-page">
            <Image
              unoptimized
              src={image.url}
              alt={`Aperçu de ${image.file.name}`}
              width={image.width}
              height={image.height}
              className="max-h-full w-auto max-w-full object-contain"
            />
          </div>
          <figcaption className="flex items-center justify-between gap-2">
            <span className="min-w-0 truncate font-medium">{image.file.name}</span>
            <button type="button" className="min-h-11 shrink-0 px-2 text-ink-muted underline" onClick={reset}>
              Retirer l&apos;image
            </button>
          </figcaption>
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-y-1 text-[13px]">
            <dt className="text-ink-muted">Dimensions</dt>
            <dd className="font-mono">{`${image.width} × ${image.height}`}</dd>
            <dt className="text-ink-muted">Poids</dt>
            <dd className="font-mono">{formatSize(image.file.size)}</dd>
            <dt className="text-ink-muted">Transparence</dt>
            <dd>{image.hasAlpha ? "Oui" : "Non"}</dd>
          </dl>
        </figure>
      )}

      <label
        data-dragging={dragging}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
        }}
        onDrop={drop}
        className="flex min-h-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-line-strong p-3 text-center outline-accent hover:border-accent has-[:focus-visible]:outline-2 data-[dragging=true]:border-accent data-[dragging=true]:bg-page"
      >
        <input
          type="file"
          accept={ACCEPTED_MIME_TYPES.join(",")}
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = ""; // sinon reprendre le même fichier ne déclenche rien
            if (file) void pick(file);
          }}
        />
        <span className="font-medium">Importer une image</span>
        <span className="text-xs text-ink-muted">{`JPEG, PNG ou WebP, ${UPLOAD_MAX_BYTES / MIB} Mo maximum`}</span>
      </label>

      <p aria-live="polite" className="text-[13px] text-ink-muted empty:hidden">
        {reading ? "Lecture de l'image…" : ""}
      </p>
      {error && (
        <p role="alert" className="text-[13px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
