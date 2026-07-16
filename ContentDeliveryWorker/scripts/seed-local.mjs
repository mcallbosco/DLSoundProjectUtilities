import { spawnSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const fixtureRoot = join(root, "fixtures", "r2");
const stateRoot = join(root, ".wrangler", "state");
const wranglerBin = join(root, "node_modules", "wrangler", "bin", "wrangler.js");

function walk(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

for (const path of walk(fixtureRoot)) {
  const key = relative(fixtureRoot, path).replaceAll("\\", "/");
  const result = spawnSync(
    process.execPath,
    [
      wranglerBin,
      "r2",
      "object",
      "put",
      `vlviewer-content/${key}`,
      "--file",
      path,
      "--local",
      "--persist-to",
      stateRoot,
    ],
    { cwd: root, stdio: "inherit" }
  );
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

console.log(`Seeded ${walk(fixtureRoot).length} local R2 fixture objects.`);
