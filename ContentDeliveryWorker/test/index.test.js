import assert from "node:assert/strict";
import test from "node:test";

import { handleRequest } from "../src/index.js";

function fakeObject(body, options = {}) {
  const bytes = new TextEncoder().encode(body);
  const range = options.range;
  const selected = range
    ? bytes.slice(range.offset, range.offset + range.length)
    : bytes;
  return {
    body: selected,
    size: bytes.length,
    range,
    httpEtag: '"test-etag"',
    uploaded: new Date("2026-07-15T00:00:00Z"),
    writeHttpMetadata(headers) {
      headers.set("Content-Type", options.contentType || "application/octet-stream");
      if (options.cacheControl) headers.set("Cache-Control", options.cacheControl);
    },
  };
}

function environment(objects) {
  return {
    CONTENT_BUCKET: {
      async head(key) {
        const object = objects.get(key);
        if (!object) return null;
        const { body: _body, ...metadata } = object;
        return metadata;
      },
      async get(key, options) {
        const object = objects.get(key);
        if (!object) return null;
        const rangeHeader = options.range.get("Range");
        if (!rangeHeader) return object;
        const match = /^bytes=(\d+)-(\d+)$/.exec(rangeHeader);
        if (!match) return object;
        return fakeObject(new TextDecoder().decode(object.body), {
          range: {
            offset: Number(match[1]),
            length: Number(match[2]) - Number(match[1]) + 1,
          },
        });
      },
    },
  };
}

test("health and CORS preflight work without R2 content", async () => {
  const env = environment(new Map());
  const health = await handleRequest(new Request("http://localhost/_health"), env);
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { ok: true });

  const preflight = await handleRequest(
    new Request("http://localhost/deadlock/manifest.json", {
      method: "OPTIONS",
      headers: { "Access-Control-Request-Headers": "Range" },
    }),
    env
  );
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("Access-Control-Allow-Origin"), "*");
  assert.match(preflight.headers.get("Access-Control-Allow-Headers"), /Range/);
});

test("JSON is mutable and binary content is immutable", async () => {
  const objects = new Map([
    ["deadlock/manifest.json", fakeObject("{}", { contentType: "application/json" })],
    ["deadlock/versions/test/audio/test.mp3", fakeObject("0123456789")],
  ]);
  const env = environment(objects);

  const json = await handleRequest(
    new Request("http://localhost/deadlock/manifest.json"),
    env
  );
  assert.equal(json.status, 200);
  assert.equal(json.headers.get("Cache-Control"), "public, max-age=0, must-revalidate");

  const binary = await handleRequest(
    new Request("http://localhost/deadlock/versions/test/audio/test.mp3"),
    env
  );
  assert.equal(binary.status, 200);
  assert.equal(binary.headers.get("Cache-Control"), "public, max-age=31536000, immutable");
});

test("all-version character route JSON is served as mutable game metadata", async () => {
  const payload = JSON.stringify({
    schemaVersion: 1,
    game: "deadlock",
    characters: ["abrams", "butcher"],
    versions: { latest: ["abrams"], historical: ["butcher"] },
  });
  const env = environment(new Map([
    ["deadlock/characters.json", fakeObject(payload, { contentType: "application/json" })],
  ]));

  const response = await handleRequest(
    new Request("http://localhost/deadlock/characters.json"),
    env,
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "public, max-age=0, must-revalidate");
  assert.deepEqual((await response.json()).characters, ["abrams", "butcher"]);
});

test("character-name mapping JSON is served as mutable game metadata", async () => {
  const payload = JSON.stringify({
    schemaVersion: 1,
    game: "deadlock",
    names: { forge: "McGinnis", mcginnis: "McGinnis" },
  });
  const env = environment(new Map([
    ["deadlock/character-names.json", fakeObject(payload, { contentType: "application/json" })],
  ]));

  const response = await handleRequest(
    new Request("http://localhost/deadlock/character-names.json"),
    env,
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "public, max-age=0, must-revalidate");
  assert.equal((await response.json()).names.forge, "McGinnis");
});

test("HEAD and byte ranges expose the expected metadata", async () => {
  const key = "deadlock/versions/test/audio/test.mp3";
  const env = environment(new Map([[key, fakeObject("0123456789")]]));

  const head = await handleRequest(new Request(`http://localhost/${key}`, { method: "HEAD" }), env);
  assert.equal(head.status, 200);
  assert.equal(head.headers.get("Content-Length"), "10");
  assert.equal(head.headers.get("Accept-Ranges"), "bytes");

  const partial = await handleRequest(
    new Request(`http://localhost/${key}`, { headers: { Range: "bytes=2-5" } }),
    env
  );
  assert.equal(partial.status, 206);
  assert.equal(partial.headers.get("Content-Range"), "bytes 2-5/10");
  assert.equal(await partial.text(), "2345");
});

test("write methods and directory-style paths are rejected", async () => {
  const env = environment(new Map());
  const put = await handleRequest(
    new Request("http://localhost/deadlock/manifest.json", { method: "PUT", body: "x" }),
    env
  );
  assert.equal(put.status, 405);

  const directory = await handleRequest(new Request("http://localhost/deadlock/"), env);
  assert.equal(directory.status, 404);
});

test("internal control objects are never exposed", async () => {
  const key = "deadlock/_internal/transcript-sync.json";
  const env = environment(new Map([
    [key, fakeObject(JSON.stringify({ lastSuccessfulCommit: "secret-state" }))],
  ]));

  for (const method of ["GET", "HEAD"]) {
    const response = await handleRequest(
      new Request(`http://localhost/${key}`, { method }),
      env,
    );
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: "not_found" });
  }

  const encoded = await handleRequest(
    new Request("http://localhost/deadlock/%5Finternal/transcript-sync.json"),
    env,
  );
  assert.equal(encoded.status, 404);
});
