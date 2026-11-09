import { expect, test, type Page, type TestInfo } from "@playwright/test";

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
