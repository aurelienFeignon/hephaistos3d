import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const ORIGIN = "http://127.0.0.1:3031";
// PNG valide de 1 × 1 pixel.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC", "base64");

let counter = 0;
const uniqueName = (info: TestInfo) => `${info.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}-${Date.now()}-${(counter += 1)}.png`;
const queue = (page: Page) => page.getByRole("region", { name: "File d'attente" });
const library = (page: Page) => page.getByRole("complementary", { name: "Bibliothèque" });
const detail = (page: Page) => page.getByRole("complementary", { name: "Détail de la génération" });
const bar = (page: Page) => page.getByRole("banner");
const stepsField = (page: Page) => page.getByRole("spinbutton", { name: "Pas", exact: true });
const generate = (page: Page) => page.getByRole("button", { name: "Générer" });
const row = (page: Page, name: string) => library(page).getByRole("button", { name: new RegExp(name.replace(/[.]/g, "\\.")) });

const setDispatch = (page: Page, paused: boolean) =>
  page.request.put("/api/v1/system/dispatch", { headers: { Origin: ORIGIN, "Content-Type": "application/json" }, data: { paused } });

async function importImage(page: Page, name: string) {
  await page.locator("input[type=file]").setInputFiles({ name, mimeType: "image/png", buffer: PNG });
  await expect(page.getByRole("img", { name: `Aperçu de ${name}` })).toBeVisible();
}

async function setStepDelay(page: Page, seconds: string) {
  await page.getByText("Avancé").click();
  await page.getByRole("spinbutton", { name: "Durée d'un pas (s)" }).fill(seconds);
}

test.beforeEach(async ({ page }) => {
  await expect
    .poll(async () => {
      const models = await (await page.request.get("/api/v1/models")).json();
      return models.find((model: { id: string }) => model.id === "fake")?.available;
    }, { timeout: 30_000 })
    .toBe(true);
  await page.goto("/");
  await page.getByRole("combobox", { name: "Modèle" }).selectOption("fake");
  await expect(stepsField(page)).toBeVisible();
});

test.afterEach(async ({ page }) => {
  await setDispatch(page, false);
  await expect.poll(async () => (await (await page.request.get("/api/v1/queue")).json()).items.length, { timeout: 30_000 }).toBe(0);
});

test("A3.1 importer, choisir Équilibré, générer, suivre les pas réels jusqu'au résultat", async ({ page }, info) => {
  const name = uniqueName(info);
  await page.getByRole("radio", { name: "Équilibré" }).check();
  await setStepDelay(page, "0.2");
  await importImage(page, name);
  await generate(page).click();

  const step = queue(page).getByText(/^Échantillonnage \d+\/50$/);
  await expect(step).toBeVisible({ timeout: 20_000 });
  const current = async () => Number(/(\d+)\/50/.exec((await step.textContent()) ?? "")?.[1]);
  const first = await current();
  await expect.poll(current, { timeout: 8_000 }).toBeGreaterThan(first);

  await expect(row(page, name).getByText("Terminé")).toBeVisible({ timeout: 30_000 });
  await row(page, name).click();
  await expect(detail(page).getByText("Tentative 1 : réussie")).toBeVisible();
  await expect(detail(page).getByRole("region", { name: "Durées par étape" })).toBeVisible();
});

test("A3.4 une valeur hors bornes est refusée par le client, puis par l'API si on la force", async ({ page }, info) => {
  const name = uniqueName(info);
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/v1/generations")) posts.push(request.url());
  });
  await importImage(page, name);
  await stepsField(page).fill("201");
  await generate(page).click();
  await expect(page.getByText("Entrez une valeur d'au plus 200.")).toBeVisible();
  expect(posts).toHaveLength(0);

  const forced = await page.request.post("/api/v1/generations", {
    headers: { Origin: ORIGIN },
    multipart: { image: { name, mimeType: "image/png", buffer: PNG }, model_id: "fake", params: JSON.stringify({ steps: 201 }) },
  });
  expect(forced.status()).toBe(422);
  expect((await forced.json()).code).toBe("PARAMS_INVALID");
});

test("A3.6 aucune interrogation quand l'onglet est masqué, reprise immédiate au retour", async ({ page }, info) => {
  const name = uniqueName(info);
  const polls: number[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/") && request.resourceType() === "fetch") polls.push(Date.now());
  });
  const setHidden = (hidden: boolean) =>
    page.evaluate((value) => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => value });
      document.dispatchEvent(new Event("visibilitychange"));
    }, hidden);

  await setStepDelay(page, "0.5");
  await importImage(page, name);
  await generate(page).click();
  await expect(queue(page).getByText(/^Échantillonnage/)).toBeVisible({ timeout: 20_000 });

  await setHidden(true);
  const hiddenFrom = polls.length;
  await page.waitForTimeout(5_000);
  expect(polls.length).toBe(hiddenFrom);

  await setHidden(false);
  await expect.poll(() => polls.length, { timeout: 1_000 }).toBeGreaterThan(hiddenFrom);
  await queue(page).getByRole("button", { name: `Annuler ${name}` }).click();
});

test("F18 une génération attend pendant la suspension et repart à la reprise", async ({ page }, info) => {
  const name = uniqueName(info);
  await bar(page).getByRole("button", { name: "Suspendre" }).click();
  await expect(bar(page).getByText("Distribution suspendue")).toBeVisible();
  await importImage(page, name);
  await generate(page).click();
  await expect(queue(page).getByText("distribution suspendue")).toBeVisible({ timeout: 4_000 });
  await bar(page).getByRole("button", { name: "Reprendre" }).click();
  await expect(row(page, name).getByText("Terminé")).toBeVisible({ timeout: 30_000 });
});

test("P3.7 annuler, relancer, protéger puis supprimer avec confirmation", async ({ page }, info) => {
  const name = uniqueName(info);
  await setDispatch(page, true);
  await importImage(page, name);
  await generate(page).click();
  await queue(page).getByRole("button", { name: `Annuler ${name}` }).click();
  await expect(row(page, name).getByText("Annulé")).toBeVisible();

  await row(page, name).click();
  await detail(page).getByRole("button", { name: "Relancer" }).click();
  await expect(detail(page).getByRole("button", { name: "Annuler" })).toBeVisible();
  const protect = detail(page).getByRole("button", { name: "Protéger" });
  await protect.click();
  await expect(protect).toHaveAttribute("aria-pressed", "true");

  await detail(page).getByRole("button", { name: "Annuler" }).click();
  await detail(page).getByRole("button", { name: "Supprimer" }).click();
  await detail(page).getByRole("button", { name: "Conserver" }).click();
  await expect(row(page, name)).toBeVisible();
  await detail(page).getByRole("button", { name: "Supprimer" }).click();
  await detail(page).getByRole("button", { name: "Confirmer la suppression" }).click();
  await expect(row(page, name)).toHaveCount(0);
  await expect(page.getByRole("complementary", { name: "Nouvelle génération" })).toBeVisible();
});

test("F15 une erreur est détaillée avec son journal", async ({ page }, info) => {
  const name = uniqueName(info);
  await setStepDelay(page, "0");
  await page.getByRole("combobox", { name: "Échec injecté" }).selectOption("NO_OBJECT");
  await importImage(page, name);
  await generate(page).click();
  await expect(row(page, name).getByText("Échoué")).toBeVisible({ timeout: 30_000 });
  await row(page, name).click();
  await expect(detail(page).getByRole("region", { name: "Erreur" })).toBeVisible();
  await detail(page).getByRole("button", { name: "Voir le journal de la tentative 1" }).click();
  await expect(detail(page).locator("pre")).toContainText("attempt_started");
});

test("P3.2 thème appliqué et mise en page sans défilement horizontal", async ({ page }) => {
  const styles = await page.evaluate(() => ({
    page: getComputedStyle(document.body).backgroundColor,
    stage: getComputedStyle(document.querySelector('[aria-label="Visualiseur 3D"]') as Element).backgroundColor,
  }));
  expect(styles).toEqual({ page: "rgb(245, 243, 239)", stage: "rgb(21, 23, 26)" });

  for (const width of [1440, 900, 390]) {
    await page.setViewportSize({ width, height: 800 });
    const [content, viewport] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    expect(content, `largeur ${width}`).toBeLessThanOrEqual(viewport);
  }
  await expect(queue(page)).toBeHidden();
  await expect(page.getByRole("button", { name: "Bibliothèque" })).toBeVisible();
});

const stage = (page: Page) => page.getByRole("region", { name: "Visualiseur 3D" });

async function completedGeneration(page: Page, info: TestInfo) {
  const name = uniqueName(info);
  await page.getByRole("radio", { name: "Rapide" }).check();
  // Peu de gaussiennes : le rendu logiciel du navigateur sans GPU reste fluide. Les gros fichiers
  // relèvent d'A4.1, vérifié sur le poste cible.
  await page.getByRole("spinbutton", { name: "Gaussiennes" }).fill("4096");
  await setStepDelay(page, "0.01");
  await importImage(page, name);
  await generate(page).click();
  await expect(row(page, name).getByText("Terminé")).toBeVisible({ timeout: 30_000 });
  return name;
}

test("P4.1 le résultat s'affiche en 3D ; A4.2 la vue tourne puis revient à l'état initial", async ({ page }, info) => {
  await expect(stage(page).getByText(/Sélectionnez une génération terminée/)).toBeVisible();
  const name = await completedGeneration(page, info);
  await row(page, name).click();

  const viewer = page.getByTestId("splat-viewer");
  await expect(viewer).toHaveAttribute("data-ready", "true", { timeout: 30_000 });
  await expect(viewer.getByText(/4\s096\s+gaussiennes/)).toBeVisible();
  const initial = await viewer.getAttribute("data-camera");
  expect(initial).toMatch(/^-?\d/);

  const box = (await viewer.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 200, box.y + box.height / 2 + 60, { steps: 10 });
  await page.mouse.up();
  await expect.poll(() => viewer.getAttribute("data-camera")).not.toBe(initial);

  await viewer.getByRole("button", { name: "Réinitialiser la vue" }).click();
  await expect.poll(() => viewer.getAttribute("data-camera")).toBe(initial);
  // L'élan de l'amortissement ne doit pas faire repartir la vue après la réinitialisation.
  await page.waitForTimeout(500);
  expect(await viewer.getAttribute("data-camera")).toBe(initial);
});

test("A4.3 fichier absent ou illisible : message explicite, pas d'écran vide", async ({ page }, info) => {
  const name = await completedGeneration(page, info);
  let answer: "missing" | "garbage" = "missing";
  await page.route(/\/api\/v1\/artifacts\/[0-9A-Z]{26}\/download$/, async (route) => {
    // Seul le PLY est altéré : la caméra (JSON) passe telle quelle.
    const response = await route.fetch();
    if (!(response.headers()["content-type"] ?? "").includes("octet-stream")) return route.fulfill({ response });
    if (answer === "missing") {
      return route.fulfill({ status: 404, contentType: "application/problem+json", body: JSON.stringify({ type: "about:blank", title: "Not Found", status: 404, detail: "Artifact file not found", code: "ARTIFACT_NOT_FOUND" }) });
    }
    // Même taille que le vrai fichier : seul le contenu est corrompu, la taille reste cohérente.
    const body = Buffer.alloc((await response.body()).length, 0x20);
    body.write("<!DOCTYPE html><p>pas un PLY</p>");
    return route.fulfill({ status: 200, contentType: "application/octet-stream", body });
  });

  await row(page, name).click();
  await expect(stage(page).getByRole("alert")).toContainText("introuvable sur le serveur");
  answer = "garbage";
  await stage(page).getByRole("button", { name: "Réessayer" }).click();
  await expect(stage(page).getByRole("alert")).toContainText("pas un PLY valide");
});

test("A4.4 les fichiers téléchargés sont ceux du serveur, octet pour octet (sha256 = ETag)", async ({ page }, info) => {
  const name = await completedGeneration(page, info);
  await row(page, name).click();
  const generations = (await (await page.request.get("/api/v1/generations")).json()).items as { id: string; source: { filename: string }; artifacts: { kind: string; sha256: string; size_bytes: number; download_url: string }[] }[];
  const generation = generations.find((item) => item.source.filename === name)!;
  const downloads = detail(page).getByRole("region", { name: "Télécharger" });

  for (const [label, kind, file] of [[/^Télécharger splat\.ply/, "SPLAT_PLY", "splat.ply"], ["Télécharger l'aperçu PNG", "PREVIEW_PNG", "preview.png"], ["Télécharger camera.json", "CAMERA_JSON", "camera.json"]] as const) {
    const artifact = generation.artifacts.find((item) => item.kind === kind)!;
    const [download] = await Promise.all([page.waitForEvent("download"), downloads.getByRole("link", { name: label }).click()]);
    expect(download.suggestedFilename()).toBe(`hephaistos-${generation.id}-${file}`);
    const bytes = await readFile((await download.path())!);
    const digest = createHash("sha256").update(bytes).digest("hex");
    // Le navigateur passe par le proxy Next : il ne doit rien altérer, ni octets ni ETag.
    expect(bytes.length).toBe(artifact.size_bytes);
    expect(digest).toBe(artifact.sha256);
    const direct = await page.request.get(artifact.download_url);
    expect(direct.headers()["etag"]).toBe(`"${digest}"`);
    expect(Buffer.compare(await direct.body(), bytes)).toBe(0);
  }
});
