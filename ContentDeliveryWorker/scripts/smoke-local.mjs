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
const manifestBody = await manifest.json();
if (manifestBody.defaultCategoriesUrl !== `${base}/deadlock/categories.json`) {
  throw new Error("Manifest game-level default categories URL is incorrect.");
}
if (manifestBody.sharedAudioBaseUrl !== `${base}/deadlock/audio/`) {
  throw new Error("Manifest shared audio base URL is incorrect.");
}
if (manifestBody.charactersUrl !== `${base}/deadlock/characters.json`) {
  throw new Error("Manifest all-version characters URL is incorrect.");
}
if (manifestBody.characterNamesUrl !== `${base}/deadlock/character-names.json`) {
  throw new Error("Manifest character-name mapping URL is incorrect.");
}
const categories = await expect("/deadlock/categories.json", 200);
const categoriesBody = await categories.json();
if (categoriesBody.defaultCategory !== "Characters") {
  throw new Error("Game-level default categories fixture is incorrect.");
}

await expect(
  "/deadlock/audio/sha256/b7/b79d01f030f61df5c45b775390f8bccf1fda7a34a515fe616ddcddc7d5c27d55.mp3",
  200,
);
const characters = await expect("/deadlock/characters.json", 200);
const charactersBody = await characters.json();
if (!charactersBody.characters.includes("abrams")) {
  throw new Error("All-version characters fixture is incorrect.");
}
const characterNames = await expect("/deadlock/character-names.json", 200);
const characterNamesBody = await characterNames.json();
if (characterNamesBody.names.forge !== "McGinnis") {
  throw new Error("Character-name mapping fixture is incorrect.");
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
