// Pile complète et jetable pour Playwright : migration, API, vrai superviseur factice, Next de production.
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const FRONTEND = resolve(import.meta.dirname, "..");
const BACKEND = resolve(FRONTEND, "../backend");
const BIN = join(BACKEND, ".venv/bin");
const API_PORT = 8010;
const WEB_PORT = 3031;
const ORIGIN = `http://127.0.0.1:${WEB_PORT}`;
const KILL_AFTER_MS = 10_000;

if (!existsSync(join(BIN, "python"))) throw new Error("Environnement backend absent : lancer `uv sync --frozen` dans backend/.");

const root = mkdtempSync(join(tmpdir(), "hephaistos-e2e-"));
for (const name of ["data/db", "data/sources", "data/results", "data/logs", "data/tmp", "models"]) mkdirSync(join(root, name), { recursive: true });

const env = {
  ...process.env,
  HEPHAISTOS_DATA_DIR: join(root, "data"),
  HEPHAISTOS_MODELS_ROOT: join(root, "models"),
  ENABLE_FAKE_ADAPTER: "true",
  GPU_MIN_FREE_VRAM_MB: "0",
  DISPATCH_PAUSED: "false",
  WORKER_SHUTDOWN_GRACE_SECONDS: "2",
  WORKER_STOP_GRACE_SECONDS: "5",
  API_ALLOWED_ORIGINS: ORIGIN,
  FRONTEND_ALLOWED_ORIGINS: ORIGIN,
  HEPHAISTOS_API_URL: `http://127.0.0.1:${API_PORT}`,
  API_ACCESS_TOKEN: randomBytes(32).toString("base64url"),
};
delete env.VIRTUAL_ENV; // uv y laisse parfois l'environnement d'un autre projet

const children = [];
let stopping = false;

function start(command, args, cwd) {
  const child = spawn(command, args, { cwd, env, stdio: "inherit" });
  child.on("exit", (code) => {
    if (stopping) return;
    console.error(`Processus arrêté prématurément (${code}) : ${command}`);
    void shutdown(1);
  });
  children.push(child);
}

async function shutdown(code) {
  if (stopping) return;
  stopping = true;
  const alive = children.filter((child) => child.exitCode === null && child.signalCode === null);
  const exited = alive.map((child) => new Promise((done) => child.once("exit", done)));
  for (const child of alive) child.kill("SIGTERM");
  const forceKill = setTimeout(() => alive.forEach((child) => child.kill("SIGKILL")), KILL_AFTER_MS);
  await Promise.all(exited);
  clearTimeout(forceKill);
  rmSync(root, { recursive: true, force: true });
  process.exit(code);
}

function waitForPort(port) {
  return new Promise((done, fail) => {
    const deadline = Date.now() + 30_000;
    const attempt = () => {
      const socket = connect(port, "127.0.0.1");
      socket.once("connect", () => {
        socket.destroy();
        done();
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() > deadline) fail(new Error(`Port ${port} injoignable`));
        else setTimeout(attempt, 200);
      });
    };
    attempt();
  });
}

process.on("SIGTERM", () => void shutdown(0));
process.on("SIGINT", () => void shutdown(0));

try {
  const migration = spawnSync(join(BIN, "alembic"), ["upgrade", "head"], { cwd: BACKEND, env, stdio: "inherit" });
  if (migration.status !== 0) throw new Error("Migration en échec");
  start(join(BIN, "uvicorn"), ["--factory", "hephaistos.api.app:create_app", "--host", "127.0.0.1", "--port", String(API_PORT), "--no-access-log"], BACKEND);
  await waitForPort(API_PORT);
  start(join(BIN, "python"), ["tests/e2e/run_fake_worker.py"], BACKEND);
  start(process.execPath, ["node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", String(WEB_PORT)], FRONTEND);
} catch (error) {
  console.error(error);
  await shutdown(1);
}
