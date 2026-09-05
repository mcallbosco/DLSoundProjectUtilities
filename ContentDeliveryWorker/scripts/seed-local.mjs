import { createHash } from "node:crypto";
import { readFile, readdir, stat } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

const BATCH_MAX_OBJECTS = 64;
const BATCH_MAX_BYTES = 8 * 1024 * 1024;
const TRANSIENT_ERROR_CODES = new Set([
  "EADDRINUSE",
  "ECONNREFUSED",
  "ECONNRESET",
  "EPIPE",
  "ETIMEDOUT",
]);
const seedWorker = `
const INTERNAL_CONCURRENCY = 8;

async function mapLimit(values, limit, callback) {
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, values.length) }, async () => {
    while (next < values.length) {
      const index = next++;
      await callback(values[index]);
    }
  });
  await Promise.all(workers);
}

async function inventory(bucket) {
  const objects = [];
  let cursor;
  do {
    const page = await bucket.list({ limit: 1000, cursor });
    objects.push(...page.objects.map((object) => ({
      key: object.key,
      size: object.size,
      etag: object.etag,
    })));
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return objects;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/inventory") {
      return Response.json(await inventory(env.CONTENT_BUCKET));
    }
    if (request.method === "POST" && url.pathname === "/batch") {
      const payload = await request.arrayBuffer();
      if (payload.byteLength < 4) return new Response("Invalid seed batch.", { status: 400 });
      const bytes = new Uint8Array(payload);
      const headerLength = new DataView(payload).getUint32(0);
      const dataOffset = 4 + headerLength;
      if (dataOffset > bytes.byteLength) {
        return new Response("Invalid seed batch header.", { status: 400 });
      }
      const entries = JSON.parse(new TextDecoder().decode(bytes.subarray(4, dataOffset)));
      let offset = dataOffset;
      for (const entry of entries) {
        entry.offset = offset;
        offset += entry.length;
      }
      if (offset !== bytes.byteLength) {
        return new Response("Invalid seed batch lengths.", { status: 400 });
      }
      await mapLimit(entries, INTERNAL_CONCURRENCY, async (entry) => {
        await env.CONTENT_BUCKET.put(
          entry.key,
          bytes.subarray(entry.offset, entry.offset + entry.length),
          {
            httpMetadata: {
              contentType: entry.contentType || "application/octet-stream",
            },
          },
        );
      });
      return Response.json({ stored: entries.length });
    }
    if (request.method === "DELETE" && url.pathname === "/objects") {
      const keys = await request.json();
      if (!Array.isArray(keys)) return new Response("Expected a key array.", { status: 400 });
      for (let index = 0; index < keys.length; index += 1000) {
        await env.CONTENT_BUCKET.delete(keys.slice(index, index + 1000));
      }
      return Response.json({ deleted: keys.length });
    }
    return new Response("Not found.", { status: 404 });
  }
};
`;

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const defaults = {
  source: join(root, "fixtures", "r2"),
  persistTo: join(root, ".wrangler", "state"),
  reset: false,
  suffix: null,
};

function parseArgs(argv) {
  const options = { ...defaults };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--source") options.source = resolve(argv[++index]);
    else if (argument === "--persist-to") options.persistTo = resolve(argv[++index]);
    else if (argument === "--reset") options.reset = true;
    else if (argument === "--suffix") options.suffix = argv[++index];
    else throw new Error(`Unknown argument: ${argument}`);
  }
  return options;
}

async function walk(rootDirectory) {
  const files = [];
  const pending = [rootDirectory];
  while (pending.length > 0) {
    const directory = pending.pop();
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) pending.push(path);
      else if (entry.isFile()) files.push(path);
    }
  }
  return files;
}

function contentType(path) {
  const lower = path.toLowerCase();
  if (lower.endsWith(".json")) return "application/json; charset=utf-8";
  if (lower.endsWith(".mp3")) return "audio/mpeg";
  if (lower.endsWith(".wav")) return "audio/wav";
  if (lower.endsWith(".ogg")) return "audio/ogg";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  return "application/octet-stream";
}

function assertSafeReset(target) {
  const resolved = resolve(target);
  const allowedRoot = resolve(root, ".wrangler");
  if (!resolved.startsWith(`${allowedRoot}${sep}`)) {
    throw new Error(`Refusing to reset state outside ${allowedRoot}: ${resolved}`);
  }
  if (!["state", "preview-state"].includes(resolved.split(sep).at(-1))) {
    throw new Error(`Refusing to reset unexpected Wrangler state directory: ${resolved}`);
  }
}

function md5(value) {
  return createHash("md5").update(value).digest("hex");
}

function errorCode(error) {
  let current = error;
  while (current && typeof current === "object") {
    if (typeof current.code === "string") return current.code;
    current = current.cause;
  }
  return null;
}

async function withRetry(operation, description) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      const code = errorCode(error);
      if (attempt >= 8 || !TRANSIENT_ERROR_CODES.has(code)) throw error;
      const wait = Math.min(250 * (2 ** (attempt - 1)), 4000);
      console.warn(`${description} hit ${code}; retrying in ${wait} ms (${attempt}/8)...`);
      await new Promise((resolvePromise) => setTimeout(resolvePromise, wait));
    }
  }
}

async function checkedDispatch(miniflare, path, init, description) {
  const response = await withRetry(
    () => miniflare.dispatchFetch(`http://seed.local${path}`, init),
    description,
  );
  if (!response.ok) {
    throw new Error(`${description} failed (${response.status}): ${await response.text()}`);
  }
  return response;
}

const options = parseArgs(process.argv.slice(2));
if (!(await stat(options.source).catch(() => null))?.isDirectory()) {
  throw new Error(`Seed source is not a directory: ${options.source}`);
}
if (options.reset) {
  assertSafeReset(options.persistTo);
}

const allFiles = await walk(options.source);
const files = options.suffix
  ? allFiles.filter((path) => relative(options.source, path).replaceAll("\\", "/").endsWith(options.suffix))
  : allFiles;
if (files.length === 0) {
  throw new Error(`No seed files matched${options.suffix ? ` suffix ${options.suffix}` : ""}.`);
}
const miniflare = new Miniflare(convertV4MiniflareOptions({
  modules: true,
  script: seedWorker,
  r2Buckets: { CONTENT_BUCKET: "vlviewer-content" },
  // Match Wrangler's local resource storage under --persist-to/v3.
  resourcePersistencePath: join(options.persistTo, "v3"),
}));

try {
  // With --reset, mirror the source tree at object level instead of deleting
  // the database first. This lets a failed large seed resume safely.
  let existing = new Map();
  if (options.reset && !options.suffix) {
    const response = await checkedDispatch(
      miniflare,
      "/inventory",
      undefined,
      "Local R2 inventory",
    );
    existing = new Map((await response.json()).map((object) => [object.key, object]));
    if (existing.size) {
      console.log(`Found ${existing.size} existing local R2 objects; unchanged objects will be reused.`);
    }
  }

  let completed = 0;
  let uploaded = 0;
  let reused = 0;
  let nextProgress = 500;
  let batch = [];
  let batchBytes = 0;

  const reportProgress = (force = false) => {
    if (!force && completed < nextProgress) return;
    console.log(
      `Prepared ${completed}/${files.length} local R2 objects `
      + `(${uploaded} written, ${reused} reused)...`,
    );
    while (nextProgress <= completed) nextProgress += 500;
  };

  const flush = async () => {
    if (!batch.length) return;
    const header = Buffer.from(JSON.stringify(batch.map((item) => ({
      key: item.key,
      length: item.data.byteLength,
      contentType: item.contentType,
    }))));
    const headerLength = Buffer.allocUnsafe(4);
    headerLength.writeUInt32BE(header.byteLength);
    const body = Buffer.concat([headerLength, header, ...batch.map((item) => item.data)]);
    const response = await checkedDispatch(
      miniflare,
      "/batch",
      {
        method: "POST",
        headers: { "content-type": "application/octet-stream" },
        body,
      },
      `Local R2 batch ending with ${batch.at(-1).key}`,
    );
    const result = await response.json();
    if (result.stored !== batch.length) {
      throw new Error(`Local R2 batch stored ${result.stored} of ${batch.length} objects.`);
    }
    completed += batch.length;
    uploaded += batch.length;
    batch = [];
    batchBytes = 0;
    reportProgress();
  };

  const sourceKeys = new Set();
  for (const path of files) {
    const key = relative(options.source, path).replaceAll("\\", "/");
    sourceKeys.add(key);
    let data;
    try {
      data = await readFile(path);
    } catch (error) {
      if (error?.code === "ENOENT") {
        throw new Error(
          `Preview source changed while it was being seeded: ${path}. `
          + "Wait for content regeneration to finish, then seed the preview again.",
          { cause: error },
        );
      }
      throw error;
    }
    const old = existing.get(key);
    const oldEtag = old?.etag?.replace(/^"|"$/g, "").toLowerCase();
    if (old && old.size === data.byteLength && oldEtag === md5(data)) {
      await flush();
      completed += 1;
      reused += 1;
      reportProgress();
      continue;
    }
    if (
      batch.length
      && (batch.length >= BATCH_MAX_OBJECTS || batchBytes + data.byteLength > BATCH_MAX_BYTES)
    ) {
      await flush();
    }
    batch.push({ key, data, contentType: contentType(path) });
    batchBytes += data.byteLength;
  }
  await flush();

  if (options.reset && !options.suffix) {
    const stale = [...existing.keys()].filter((key) => !sourceKeys.has(key));
    for (let index = 0; index < stale.length; index += 5000) {
      const keys = stale.slice(index, index + 5000);
      await checkedDispatch(
        miniflare,
        "/objects",
        {
          method: "DELETE",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(keys),
        },
        "Local R2 stale-object cleanup",
      );
    }
    if (stale.length) console.log(`Removed ${stale.length} stale local R2 objects.`);
  }
  reportProgress(true);
} finally {
  await miniflare.dispose();
}

console.log(`Seeded ${files.length} objects from ${options.source}.`);
