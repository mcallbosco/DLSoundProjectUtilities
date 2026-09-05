import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

const execute = promisify(execFile);
const root = fileURLToPath(new URL("..", import.meta.url));

test("local seeding persists content and reuses it on the next seed", async () => {
  await mkdir(join(root, ".wrangler"), { recursive: true });
  const temporary = await mkdtemp(join(root, ".wrangler", "seed-test-"));
  const state = join(temporary, "state");
  const args = [join(root, "scripts", "seed-local.mjs"), "--persist-to", state, "--reset"];
  let reader;
  try {
    await execute(process.execPath, args, { cwd: root, timeout: 30_000 });
    const repeated = await execute(process.execPath, args, { cwd: root, timeout: 30_000 });
    assert.match(repeated.stdout, /0 written, \d+ reused/);

    reader = new Miniflare(convertV4MiniflareOptions({
      modules: true,
      script: `export default { async fetch(request, env) {
        const object = await env.CONTENT_BUCKET.get("deadlock/manifest.json");
        return object ? new Response(object.body) : new Response("Missing", { status: 404 });
      } };`,
      r2Buckets: { CONTENT_BUCKET: "vlviewer-content" },
      resourcePersistencePath: join(state, "v3"),
    }));
    const response = await reader.dispatchFetch("http://localhost/manifest.json");
    assert.equal(response.status, 200);
    assert.equal((await response.json()).game, "deadlock");
  } finally {
    if (reader) await reader.dispose();
    await rm(temporary, { recursive: true, force: true });
  }
});
