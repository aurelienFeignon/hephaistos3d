import { z } from "zod";

const MIB = 1024 * 1024;

// Valeurs par défaut du backend, qui ne les expose pas : l'API reste seule juge si elles sont modifiées.
export const UPLOAD_MAX_BYTES = 20 * MIB;
export const UPLOAD_MAX_PIXELS = 40_000_000;
export const ACCEPTED_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

// Une transparence d'un seul pixel peut échapper à cette réduction ; l'indication reste informative.
const ALPHA_SCAN_MAX_SIDE = 1024;

const FORMATS = ["jpeg", "png", "webp"] as const;
export type ImageFormat = (typeof FORMATS)[number];

const fileFacts = z.object({
  size: z.number().max(UPLOAD_MAX_BYTES, `Cette image dépasse ${UPLOAD_MAX_BYTES / MIB} Mo. Choisissez un fichier plus léger.`),
  format: z.enum(FORMATS, { error: "Ce fichier n'est pas une image JPEG, PNG ou WebP." }),
});

const pixelFacts = z
  .object({ width: z.number(), height: z.number() })
  .refine(({ width, height }) => width * height <= UPLOAD_MAX_PIXELS, {
    error: `Cette image dépasse ${UPLOAD_MAX_PIXELS / 1_000_000} mégapixels. Choisissez une image plus petite.`,
  });

export type DecodedImage = {
  url: string;
  width: number;
  height: number;
  hasTransparency: () => boolean;
  release: () => void;
};

export type ImageDecoder = (file: File) => Promise<DecodedImage>;

export type InspectedImage = {
  file: File;
  url: string;
  width: number;
  height: number;
  format: ImageFormat;
  hasAlpha: boolean;
  release: () => void;
};

export type Inspection = { ok: true; image: InspectedImage } | { ok: false; error: string };

const startsWith = (bytes: Uint8Array, signature: readonly number[], offset = 0) =>
  signature.every((byte, index) => bytes[index + offset] === byte);

const ascii = (text: string) => [...text].map((char) => char.charCodeAt(0));

// Le navigateur déduit `File.type` de l'extension : seuls les octets disent ce qu'est le fichier.
export function detectFormat(bytes: Uint8Array): ImageFormat | null {
  if (startsWith(bytes, [0xff, 0xd8, 0xff])) return "jpeg";
  if (startsWith(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return "png";
  if (startsWith(bytes, ascii("RIFF")) && startsWith(bytes, ascii("WEBP"), 8)) return "webp";
  return null;
}

export async function inspectImage(file: File, decode: ImageDecoder = decodeInBrowser): Promise<Inspection> {
  const head = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  const facts = fileFacts.safeParse({ size: file.size, format: detectFormat(head) });
  if (!facts.success) return { ok: false, error: facts.error.issues[0].message };

  let decoded: DecodedImage;
  try {
    decoded = await decode(file);
  } catch {
    return { ok: false, error: "Cette image est illisible ou corrompue." };
  }

  const { url, width, height, release } = decoded;
  const pixels = pixelFacts.safeParse({ width, height });
  if (!pixels.success) {
    release();
    return { ok: false, error: pixels.error.issues[0].message };
  }

  try {
    const hasAlpha = facts.data.format !== "jpeg" && decoded.hasTransparency();
    return { ok: true, image: { file, url, width, height, format: facts.data.format, hasAlpha, release } };
  } catch {
    release();
    return { ok: false, error: "Cette image est illisible ou corrompue." };
  }
}

function hasTransparentPixel(image: HTMLImageElement): boolean {
  const scale = Math.min(1, ALPHA_SCAN_MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("contexte 2D indisponible");
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  for (let alpha = 3; alpha < data.length; alpha += 4) {
    if (data[alpha] < 255) return true;
  }
  return false;
}

// `<img>` lit les dimensions avant le décodage complet et applique l'orientation EXIF, comme le backend.
export function decodeInBrowser(file: File): Promise<DecodedImage> {
  const url = URL.createObjectURL(file);
  const image = new Image();
  return new Promise((resolve, reject) => {
    image.onload = () =>
      resolve({
        url,
        width: image.naturalWidth,
        height: image.naturalHeight,
        hasTransparency: () => hasTransparentPixel(image),
        release: () => URL.revokeObjectURL(url),
      });
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("image illisible"));
    };
    image.src = url;
  });
}
