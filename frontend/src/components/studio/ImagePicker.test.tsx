// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DecodedImage } from "@/lib/image/inspect";
import { ImagePicker } from "./ImagePicker";

afterEach(cleanup);

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13];
const png = (name = "fauteuil.png") => new File([new Uint8Array(PNG), new Uint8Array(1024 * 1024 * 2)], name);
const notAnImage = () => new File(["du texte"], "faux.png");

function decoder() {
  const releases: ReturnType<typeof vi.fn>[] = [];
  const decode = vi.fn(async (): Promise<DecodedImage> => {
    const release = vi.fn();
    releases.push(release);
    return { url: `blob:${releases.length}`, width: 768, height: 512, hasTransparency: () => true, release };
  });
  return { decode, releases };
}

const input = () => screen.getByLabelText(/Importer une image/) as HTMLInputElement;
const user = () => userEvent.setup({ applyAccept: false });
const preview = (name: string) => screen.findByRole("img", { name: `Aperçu de ${name}` });

describe("ImagePicker", () => {
  it("n'accepte que les trois formats dans le sélecteur", () => {
    render(<ImagePicker decode={decoder().decode} />);
    expect(input().accept).toBe("image/jpeg,image/png,image/webp");
  });

  it("affiche l'aperçu, les dimensions, le poids et la transparence", async () => {
    render(<ImagePicker decode={decoder().decode} />);
    await user().upload(input(), png());
    expect((await preview("fauteuil.png")).getAttribute("src")).toBe("blob:1");
    expect(screen.getByText("768 × 512")).toBeTruthy();
    expect(screen.getByText("2,0 Mo")).toBeTruthy();
    expect(screen.getByText("Oui")).toBeTruthy();
  });

  it("explique un refus et ne garde pas l'image précédente", async () => {
    const { decode, releases } = decoder();
    render(<ImagePicker decode={decode} />);
    const u = user();
    await u.upload(input(), png());
    await preview("fauteuil.png");
    await u.upload(input(), notAnImage());
    expect((await screen.findByRole("alert")).textContent).toBe("Ce fichier n'est pas une image JPEG, PNG ou WebP.");
    expect(screen.queryByRole("img")).toBeNull();
    expect(releases[0]).toHaveBeenCalledOnce();
  });

  it("efface le refus quand une image valide arrive", async () => {
    render(<ImagePicker decode={decoder().decode} />);
    const u = user();
    await u.upload(input(), notAnImage());
    await screen.findByRole("alert");
    await u.upload(input(), png());
    await preview("fauteuil.png");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("libère l'image remplacée, retirée ou encore affichée au démontage", async () => {
    const { decode, releases } = decoder();
    const view = render(<ImagePicker decode={decode} />);
    const u = user();
    await u.upload(input(), png("a.png"));
    await preview("a.png");
    await u.upload(input(), png("b.png"));
    await preview("b.png");
    expect(releases[0]).toHaveBeenCalledOnce();
    await u.click(screen.getByRole("button", { name: "Retirer l'image" }));
    expect(screen.queryByRole("img")).toBeNull();
    expect(releases[1]).toHaveBeenCalledOnce();
    await u.upload(input(), png("c.png"));
    await preview("c.png");
    view.unmount();
    expect(releases[2]).toHaveBeenCalledOnce();
  });

  it("accepte une image déposée et refuse plusieurs fichiers d'un coup", async () => {
    render(<ImagePicker decode={decoder().decode} />);
    const zone = input().closest("label") as HTMLLabelElement;
    fireEvent.drop(zone, { dataTransfer: { files: [png()] } });
    await preview("fauteuil.png");
    fireEvent.drop(zone, { dataTransfer: { files: [png("a.png"), png("b.png")] } });
    expect((await screen.findByRole("alert")).textContent).toBe("Déposez une seule image à la fois.");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("permet de reprendre le même fichier après l'avoir retiré", async () => {
    render(<ImagePicker decode={decoder().decode} />);
    const u = user();
    await u.upload(input(), png());
    await preview("fauteuil.png");
    expect(input().value).toBe("");
  });

  it("ignore une lecture lente dépassée par un choix plus récent", async () => {
    const release = vi.fn();
    let finishSlow: (image: DecodedImage) => void = () => {};
    const decode = vi.fn()
      .mockImplementationOnce(() => new Promise<DecodedImage>((resolve) => { finishSlow = resolve; }))
      .mockImplementationOnce(async (): Promise<DecodedImage> => ({ url: "blob:recent", width: 1, height: 1, hasTransparency: () => false, release: () => {} }));
    render(<ImagePicker decode={decode} />);
    const u = user();
    await u.upload(input(), png("lente.png"));
    await u.upload(input(), png("recente.png"));
    await preview("recente.png");
    finishSlow({ url: "blob:lente", width: 1, height: 1, hasTransparency: () => false, release });
    await vi.waitFor(() => expect(release).toHaveBeenCalledOnce());
    expect(screen.getByRole("img").getAttribute("alt")).toBe("Aperçu de recente.png");
  });
});
