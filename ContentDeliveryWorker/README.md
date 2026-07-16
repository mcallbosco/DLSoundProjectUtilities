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

The future website runtime-content provider should accept a development base URL
override such as:

```text
NEXT_PUBLIC_VLVIEWER_CONTENT_BASE_URL=http://127.0.0.1:8787
```

The Worker sends public read-only CORS headers, so localhost sites on any port
can request the local or production content endpoint.

## Production

The Worker has an R2 binding named `CONTENT_BUCKET`, with Cloudflare Workers
Caching enabled. JSON is always served with revalidation, while binary assets
default to one-year immutable caching. R2 conditional and range request headers
are forwarded so audio seeking and normal HTTP validation continue to work.
