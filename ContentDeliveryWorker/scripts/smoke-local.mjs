const base = process.env.VLVIEWER_LOCAL_CDN || "http://127.0.0.1:8787";

async function expect(path, expectedStatus, init) {
  const response = await fetch(`${base}${path}`, init);
  if (response.status !== expectedStatus) {
    throw new Error(`${path}: expected ${expectedStatus}, received ${response.status}`);
  }
  return response;
}

await expect("/_health", 200);
const manifest = await expect("/deadlock/manifest.json", 200);
if (manifest.headers.get("access-control-allow-origin") !== "*") {
  throw new Error("Manifest CORS header is missing.");
}
await expect("/deadlock/versions/deadlock-local/range-test.bin", 200, { method: "HEAD" });
const partial = await expect(
  "/deadlock/versions/deadlock-local/range-test.bin",
  206,
  { headers: { Range: "bytes=2-5" } }
);
if ((await partial.text()) !== "2345") throw new Error("Range response body is incorrect.");
await expect("/deadlock/missing.json", 404);

console.log(`Local CDN smoke tests passed against ${base}.`);
