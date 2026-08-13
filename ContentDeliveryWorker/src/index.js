const IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable";
const MUTABLE_JSON_CACHE_CONTROL = "public, max-age=0, must-revalidate";
const CORS_EXPOSE_HEADERS = [
  "Accept-Ranges",
  "Cache-Control",
  "Content-Length",
  "Content-Range",
  "ETag",
  "Last-Modified",
].join(", ");

function addPublicHeaders(headers) {
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Access-Control-Expose-Headers", CORS_EXPOSE_HEADERS);
  headers.set("Cross-Origin-Resource-Policy", "cross-origin");
  headers.set("X-Content-Type-Options", "nosniff");
  return headers;
}

function jsonResponse(payload, status = 200, extraHeaders = {}) {
  const headers = addPublicHeaders(new Headers(extraHeaders));
  headers.set("Content-Type", "application/json; charset=utf-8");
  headers.set("Cache-Control", "no-store");
  return new Response(JSON.stringify(payload), { status, headers });
}

function objectKeyFromUrl(url) {
  if (url.pathname === "/" || url.pathname.endsWith("/")) return null;
  let key;
  try {
    key = decodeURIComponent(url.pathname.slice(1));
  } catch {
    return null;
  }
  if (!key || key.includes("\0") || key.includes("\\")) return null;
  const firstSegment = key.split("/", 1)[0];
  if (!/^[a-z0-9][a-z0-9-]*$/.test(firstSegment)) return null;
  return key;
}

function isInternalObjectKey(key) {
  const segments = key.split("/");
  return segments.length > 1 && segments[1] === "_internal";
}

function setObjectHeaders(headers, object, key) {
  object.writeHttpMetadata(headers);
  headers.set("ETag", object.httpEtag);
  headers.set("Last-Modified", object.uploaded.toUTCString());
  headers.set("Accept-Ranges", "bytes");
  if (key.toLowerCase().endsWith(".json")) {
    headers.set("Cache-Control", MUTABLE_JSON_CACHE_CONTROL);
  } else if (!headers.has("Cache-Control")) {
    headers.set("Cache-Control", IMMUTABLE_CACHE_CONTROL);
  }
  addPublicHeaders(headers);
}

function preflightResponse(request) {
  const requestedHeaders = request.headers.get("Access-Control-Request-Headers");
  const headers = addPublicHeaders(new Headers());
  headers.set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
  headers.set(
    "Access-Control-Allow-Headers",
    requestedHeaders || "Range, If-None-Match, If-Modified-Since"
  );
  headers.set("Access-Control-Max-Age", "86400");
  headers.set("Cache-Control", "public, max-age=86400");
  return new Response(null, { status: 204, headers });
}

function conditionalFailureStatus(request) {
  return request.headers.has("If-None-Match") || request.headers.has("If-Modified-Since")
    ? 304
    : 412;
}

function applyRangeHeaders(headers, object) {
  if (!object.range || typeof object.range.offset !== "number") return false;
  const length = object.range.length;
  const start = object.range.offset;
  const end = start + length - 1;
  headers.set("Content-Range", `bytes ${start}-${end}/${object.size}`);
  headers.set("Content-Length", String(length));
  return true;
}

export async function handleRequest(request, env) {
  const url = new URL(request.url);

  if (url.pathname === "/_health") {
    if (request.method === "OPTIONS") return preflightResponse(request);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return jsonResponse(
        { error: "method_not_allowed" },
        405,
        { Allow: "GET, HEAD, OPTIONS" }
      );
    }
    const body = request.method === "HEAD" ? null : JSON.stringify({ ok: true });
    const headers = addPublicHeaders(new Headers({
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    }));
    return new Response(body, { status: 200, headers });
  }

  if (request.method === "OPTIONS") return preflightResponse(request);
  if (request.method !== "GET" && request.method !== "HEAD") {
    return jsonResponse(
      { error: "method_not_allowed" },
      405,
      { Allow: "GET, HEAD, OPTIONS" }
    );
  }

  const key = objectKeyFromUrl(url);
  if (!key || isInternalObjectKey(key)) {
    return jsonResponse({ error: "not_found" }, 404);
  }

  if (request.method === "HEAD") {
    const object = await env.CONTENT_BUCKET.head(key);
    if (object === null) return jsonResponse({ error: "not_found" }, 404);
    const headers = new Headers();
    setObjectHeaders(headers, object, key);
    headers.set("Content-Length", String(object.size));
    return new Response(null, { status: 200, headers });
  }

  const object = await env.CONTENT_BUCKET.get(key, {
    onlyIf: request.headers,
    range: request.headers,
  });
  if (object === null) return jsonResponse({ error: "not_found" }, 404);

  const headers = new Headers();
  setObjectHeaders(headers, object, key);
  if (!("body" in object)) {
    return new Response(null, {
      status: conditionalFailureStatus(request),
      headers,
    });
  }

  const isPartial = request.headers.has("Range") && applyRangeHeaders(headers, object);
  if (!isPartial) headers.set("Content-Length", String(object.size));
  return new Response(object.body, {
    status: isPartial ? 206 : 200,
    headers,
  });
}

export default {
  fetch: handleRequest,
};
