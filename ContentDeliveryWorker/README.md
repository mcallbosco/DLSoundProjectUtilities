# VLViewer Content Delivery Worker

This Worker exposes the private `vlviewer-content` R2 bucket at
`https://cdn.vlviewer.com/<game>/...`. It is deliberately read-only: uploads use
the R2 S3-compatible API through the Python publisher.

## Local development

Install dependencies, seed the small local R2 fixture, and start Wrangler:

```powershell
npm install
npm run seed:local
npm run dev
```

Wrangler listens on `http://127.0.0.1:8787` by default. In a second terminal:

```powershell
npm run smoke:local
```

Wrangler's local R2 storage is persisted under `.wrangler/state` and does not
touch the production bucket. Run `npm run seed:local` again whenever the fixture
needs to be restored.

Historical Content previews use a separate `.wrangler/preview-state` store.
The bulk seeder accepts a generated content directory and loads it through one
Miniflare process, which is substantially faster than launching Wrangler once
per audio object:

```powershell
npm run seed:preview -- --source D:\VLViewerHistoricalData\preview-content
npm run dev:preview
```

`seed:preview` mirrors only the R2 objects in `.wrangler/preview-state`. It does
not read Cloudflare credentials or access the production bucket. Objects are
sent to Miniflare in bounded batches to avoid exhausting Windows loopback
ports. If a large seed is interrupted, run the same command again: unchanged
objects are verified by size and ETag, reused, and stale objects are removed
after the source tree is ready.

The future website runtime-content provider should accept a development base URL
override such as:

```text
NEXT_PUBLIC_VLVIEWER_CONTENT_BASE_URL=http://127.0.0.1:8787
```

The Worker sends public read-only CORS headers, so localhost sites on any port
can request the local or production content endpoint.

The fixture also includes the per-game category fallback at
`deadlock/categories.json`. `deadlock/manifest.json` advertises it through
`defaultCategoriesUrl`; individual versions only need `categoriesUrl` when they
override the game default.

The per-game all-version character route index is available at
`deadlock/characters.json` and is advertised through the manifest's
`charactersUrl`. It is a small mutable JSON object maintained by the publisher.
The Worker serves it through the normal R2 object route; it does not aggregate
large version documents on request.

The per-game display-name and alias mapping is available at
`deadlock/character-names.json` and is advertised through
`characterNamesUrl`. It is also mutable JSON maintained by the publisher. The
static website build and browser request the same object through the Worker's
normal R2 route.

Shared audio is stored at `<game>/audio/sha256/<prefix>/<hash>.mp3`. The game
manifest advertises `<game>/audio/` through `sharedAudioBaseUrl`, and generated
line JSON supplies the remaining `audioKey`. The Worker serves these immutable
objects through the same streaming and byte-range path used by legacy audio.

## Production

The Worker has an R2 binding named `CONTENT_BUCKET`, with Cloudflare Workers
Caching enabled. JSON is always served with revalidation, while binary assets
default to one-year immutable caching. R2 conditional and range request headers
are forwarded so audio seeking and normal HTTP validation continue to work.
