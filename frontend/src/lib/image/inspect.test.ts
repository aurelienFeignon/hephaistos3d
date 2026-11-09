import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { detectFormat, inspectImage, UPLOAD_MAX_BYTES, UPLOAD_MAX_PIXELS, type DecodedImage } from "./inspect";

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13];
const JPEG = [0xff, 0xd8, 0xff, 0xe0, 0, 16, 0x4a, 0x46, 0x49, 0x46, 0, 1];
const WEBP = [...Buffer.from("RIFF"), 1, 0, 0, 0, ...Buffer.from("WEBP")];

function fileOf(head: number[], name = "image.png", size = head.length) {
  return new File([new Uint8Array(head), new Uint8Array(Math.max(0, size - head.length))], name);
}

function decoderOf(width: number, height: number, transparent = false) {
  const release = vi.fn();
  const decoded: DecodedImage = { url: "blob:essai", width, height, hasTransparency: () => transparent, release };
  return { decode: vi.fn(async () => decoded), release };
}

describe("detectFormat", () => {
  it.each([["jpeg", JPEG], ["png", PNG], ["webp", WEBP]])("reconnaît %s à ses octets", (format, head) => {
    expect(detectFormat(new Uint8Array(head))).toBe(format);
  });

  it.each([
    ["texte", [...Buffer.from("ceci n'est pas une image")]],
    ["GIF", [...Buffer.from("GIF89a"), 0, 0, 0, 0, 0, 0]],
    ["RIFF qui n'est pas du WebP", [...Buffer.from("RIFF"), 1, 0, 0, 0, ...Buffer.from("WAVE")]],
    ["en-tête PNG tronqué", PNG.slice(0, 4)],
    ["vide", []],
  ])("refuse %s", (_label, head) => {
    expect(detectFormat(new Uint8Array(head))).toBeNull();
  });
});

describe("inspectImage", () => {
  it("rend les faits d'une image valide", async () => {
    const file = fileOf(PNG);
    const { decode } = decoderOf(768, 512, true);
    const result = await inspectImage(file, decode);
    expect(result).toMatchObject({ ok: true, image: { file, url: "blob:essai", width: 768, height: 512, format: "png", hasAlpha: true } });
  });

  it("ne cherche pas de transparence dans un JPEG", async () => {
    const hasTransparency = vi.fn(() => true);
    const decode = async (): Promise<DecodedImage> => ({ url: "u", width: 1, height: 1, hasTransparency, release: () => {} });
    const result = await inspectImage(fileOf(JPEG, "photo.jpg"), decode);
    expect(result).toMatchObject({ ok: true, image: { format: "jpeg", hasAlpha: false } });
    expect(hasTransparency).not.toHaveBeenCalled();
  });

  it("refuse un fichier renommé .png sans le décoder", async () => {
    const { decode } = decoderOf(10, 10);
    const result = await inspectImage(fileOf([...Buffer.from("pas une image du tout")], "faux.png"), decode);
    expect(result).toEqual({ ok: false, error: "Ce fichier n'est pas une image JPEG, PNG ou WebP." });
    expect(decode).not.toHaveBeenCalled();
  });

  it("accepte exactement la taille maximale et refuse un octet de plus", async () => {
    expect((await inspectImage(fileOf(PNG, "a.png", UPLOAD_MAX_BYTES), decoderOf(10, 10).decode)).ok).toBe(true);
    const { decode } = decoderOf(10, 10);
    const result = await inspectImage(fileOf(PNG, "a.png", UPLOAD_MAX_BYTES + 1), decode);
    expect(result).toEqual({ ok: false, error: "Cette image dépasse 20 Mo. Choisissez un fichier plus léger." });
    expect(decode).not.toHaveBeenCalled();
  });

  it("accepte exactement le plafond de pixels et libère une image qui le dépasse", async () => {
    expect((await inspectImage(fileOf(PNG), decoderOf(UPLOAD_MAX_PIXELS, 1).decode)).ok).toBe(true);
    const { decode, release } = decoderOf(UPLOAD_MAX_PIXELS + 1, 1);
    const result = await inspectImage(fileOf(PNG), decode);
    expect(result).toEqual({ ok: false, error: "Cette image dépasse 40 mégapixels. Choisissez une image plus petite." });
    expect(release).toHaveBeenCalledOnce();
  });

  it("signale une image que le navigateur ne sait pas décoder", async () => {
    const result = await inspectImage(fileOf(PNG), async () => { throw new Error("corrompue"); });
    expect(result).toEqual({ ok: false, error: "Cette image est illisible ou corrompue." });
  });

  it("libère l'image si l'analyse de transparence échoue", async () => {
    const release = vi.fn();
    const decode = async (): Promise<DecodedImage> => ({
      url: "u", width: 1, height: 1, release,
      hasTransparency: () => { throw new Error("canvas"); },
    });
    expect((await inspectImage(fileOf(PNG), decode)).ok).toBe(false);
    expect(release).toHaveBeenCalledOnce();
  });
});

describe("limites", () => {
  it("restent alignées sur les valeurs par défaut du backend", () => {
    const config = readFileSync(new URL("../../../../backend/src/hephaistos/config.py", import.meta.url), "utf8");
    const read = (name: string) => Number(new RegExp(`${name}: int = Field\\(([0-9_]+)`).exec(config)?.[1].replaceAll("_", ""));
    expect(UPLOAD_MAX_BYTES).toBe(read("upload_max_bytes"));
    expect(UPLOAD_MAX_PIXELS).toBe(read("upload_max_pixels"));
  });
});
