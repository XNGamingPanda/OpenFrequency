/**
 * OpenFrequency Cloudflare Workers Backend
 *
 * Routes:
 *   GET  /health              — health check (no auth)
 *   GET  /api/version         — latest release info (cached 5 min)
 *   GET  /dl/latest/:asset    — proxy latest release asset download
 *   GET  /dl/:tag/:asset      — proxy specific tagged release asset download
 *   POST /api/crash           — store crash report in KV
 *   POST /api/feedback        — store feedback in KV
 *
 * Environment variables (set via wrangler.toml [vars] or `wrangler secret put`):
 *   CLIENT_TOKEN         — shared secret, required in X-OF-Token header
 *   GITHUB_OWNER         — GitHub repo owner
 *   GITHUB_REPO          — GitHub repo name
 *   GITHUB_TOKEN         — (optional) GitHub PAT for higher API rate limits
 *   MIN_REQUIRED_VERSION — semver string, default "0.0.0"
 *
 * KV binding: OF_KV
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-OF-Token",
  "Access-Control-Expose-Headers": "Content-Type, X-OF-Token",
};

const RATE_LIMITS = {
  version:  { perIp: 300,   global: null  },
  download: { perIp: 5,     global: 500   },
  crash:    { perIp: 30,    global: 10000 },
  feedback: { perIp: 15,    global: null  },
};

const KV_TTL = {
  crash:     7776000,  // 90 days
  feedback:  31536000, // 1 year
  rateLimit: 86400,    // 24 hours
};

const VERSION_CACHE_TTL = 300; // 5 minutes

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default {
  async fetch(request, env, ctx) {
    // Handle CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    try {
      // Health check — no auth required
      if (request.method === "GET" && path === "/health") {
        return corsJSON({ status: "ok", ts: new Date().toISOString() }, 200);
      }

      // All other routes require authentication
      const authErr = requireToken(request, env);
      if (authErr) return authErr;

      // Router
      if (request.method === "GET" && path === "/api/version") {
        return handleVersion(request, env, ctx);
      }

      const dlLatest = path.match(/^\/dl\/latest\/(.+)$/);
      if (request.method === "GET" && dlLatest) {
        return handleDownload(request, env, ctx, null, dlLatest[1]);
      }

      const dlTagged = path.match(/^\/dl\/([^/]+)\/(.+)$/);
      if (request.method === "GET" && dlTagged) {
        return handleDownload(request, env, ctx, dlTagged[1], dlTagged[2]);
      }

      if (request.method === "POST" && path === "/api/crash") {
        return handleCrash(request, env, ctx);
      }

      if (request.method === "POST" && path === "/api/feedback") {
        return handleFeedback(request, env, ctx);
      }

      return corsJSON({ error: "Not Found" }, 404);
    } catch (err) {
      console.error("Unhandled error:", err);
      return corsJSON({ error: "Internal Server Error" }, 500);
    }
  },
};

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

function requireToken(request, env) {
  const token = request.headers.get("X-OF-Token");
  if (!env.CLIENT_TOKEN) {
    // Misconfigured — fail closed
    return corsJSON({ error: "Server misconfigured: missing CLIENT_TOKEN" }, 500);
  }
  if (!token || token !== env.CLIENT_TOKEN) {
    return corsJSON({ error: "Unauthorized" }, 401);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Rate limiting
// ---------------------------------------------------------------------------

function getClientIP(request) {
  return (
    request.headers.get("CF-Connecting-IP") ||
    request.headers.get("X-Forwarded-For")?.split(",")[0].trim() ||
    "unknown"
  );
}

function todayUTC() {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD
}

/**
 * Check and increment rate limit counters.
 * Returns { limited: false } or { limited: true, globalQuota: bool, response: Response }
 */
async function checkRateLimit(env, group, ip) {
  const date = todayUTC();
  const ipKey = `rl:${ip}:${group}:${date}`;
  const globalKey = `global:${group}:${date}`;
  const limits = RATE_LIMITS[group];

  // Per-IP check
  const ipCountRaw = await env.OF_KV.get(ipKey);
  const ipCount = ipCountRaw ? parseInt(ipCountRaw, 10) : 0;

  if (ipCount >= limits.perIp) {
    return {
      limited: true,
      globalQuota: false,
      response: corsJSON(
        { error: "Rate limit exceeded", group, limit: limits.perIp, window: "daily" },
        429
      ),
    };
  }

  // Global check (download only)
  if (limits.global !== null) {
    const globalCountRaw = await env.OF_KV.get(globalKey);
    const globalCount = globalCountRaw ? parseInt(globalCountRaw, 10) : 0;

    if (globalCount >= limits.global) {
      if (group === "download") {
        return {
          limited: true,
          globalQuota: true,
          response: corsJSON(
            {
              error: "Global download quota reached. Please download directly from GitHub.",
              github_releases: `https://github.com/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/releases`,
            },
            503
          ),
        };
      }
      // crash: silently accept but flag it
      return { limited: false, globalQuota: true };
    }

    // Increment global counter (fire and forget)
    const newGlobal = globalCount + 1;
    env.OF_KV.put(globalKey, String(newGlobal), { expirationTtl: KV_TTL.rateLimit });
  }

  // Increment per-IP counter (fire and forget)
  const newIp = ipCount + 1;
  env.OF_KV.put(ipKey, String(newIp), { expirationTtl: KV_TTL.rateLimit });

  return { limited: false, globalQuota: false };
}

// ---------------------------------------------------------------------------
// GitHub helpers
// ---------------------------------------------------------------------------

function githubHeaders(env) {
  const headers = new Headers({
    "Accept": "application/vnd.github+json",
    "User-Agent": "OpenFrequency-Workers/1.0",
    "X-GitHub-Api-Version": "2022-11-28",
  });
  if (env.GITHUB_TOKEN) {
    headers.set("Authorization", `Bearer ${env.GITHUB_TOKEN}`);
  }
  return headers;
}

async function fetchLatestRelease(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/releases/latest`;
  const res = await fetch(url, { headers: githubHeaders(env) });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`GitHub API error ${res.status}: ${body}`);
  }
  return res.json();
}

async function fetchReleaseByTag(env, tag) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/releases/tags/${tag}`;
  const res = await fetch(url, { headers: githubHeaders(env) });
  if (!res.ok) {
    throw new Error(`GitHub API error ${res.status} for tag ${tag}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Release parsing helpers
// ---------------------------------------------------------------------------

/**
 * Extract text between <!-- TAG --> and <!-- /TAG --> in markdown body.
 * Returns empty string if not found.
 */
function extractBlock(body, tag) {
  if (!body) return "";
  const re = new RegExp(`<!--\\s*${tag}\\s*-->([\\s\\S]*?)<!--\\s*/${tag}\\s*-->`, "i");
  const m = body.match(re);
  return m ? m[1].trim() : "";
}

/**
 * Parse SHA256 checksums from release body.
 * Supports lines like:  abc123def456...  filename.exe
 * Returns a Map of filename -> sha256hex.
 */
function parseChecksums(body) {
  const map = new Map();
  if (!body) return map;
  // Match lines: <64 hex chars>  <filename>
  const re = /^([0-9a-fA-F]{64})\s{1,2}(\S+)$/gm;
  let m;
  while ((m = re.exec(body)) !== null) {
    map.set(m[2].trim(), m[1].toLowerCase());
  }
  return map;
}

/**
 * Detect asset platform and build structured assets object.
 * Looks for .exe/.msi for win_x64.
 * Tries to find matching .sha256 file or pulls from body checksums.
 */
function buildAssetsMap(release, workerBaseUrl) {
  const assets = {};
  const body = release.body || "";
  const checksumMap = parseChecksums(body);

  const platformRules = [
    {
      key: "win_x64",
      match: (name) =>
        (name.endsWith(".exe") || name.endsWith(".msi")) &&
        !name.endsWith(".sha256") &&
        (name.includes("win") ||
          name.includes("Win") ||
          name.includes("x64") ||
          name.includes("setup") ||
          name.includes("installer") ||
          // fallback: any exe if no platform hint
          true),
    },
    // Future: add mac_arm64, linux_x64 etc.
  ];

  // Build a lookup map of asset name -> asset object
  const assetByName = new Map();
  for (const asset of release.assets || []) {
    assetByName.set(asset.name, asset);
  }

  for (const rule of platformRules) {
    // Find first matching asset that isn't already claimed
    const match = (release.assets || []).find(
      (a) => rule.match(a.name) && !a.name.endsWith(".sha256")
    );
    if (!match) continue;

    // SHA256: prefer inline checksums map, then look for <name>.sha256 asset
    let sha256 = checksumMap.get(match.name) || "";

    if (!sha256) {
      const sha256Asset = assetByName.get(match.name + ".sha256");
      if (sha256Asset) {
        // We can't fetch at parse time; record that a .sha256 asset exists
        sha256 = `fetch:${match.name}.sha256`;
      }
    }

    const tag = release.tag_name;
    assets[rule.key] = {
      filename: match.name,
      size: match.size,
      sha256: sha256 || null,
      dl_path: `${workerBaseUrl}/dl/${encodeURIComponent(tag)}/${encodeURIComponent(match.name)}`,
    };
  }

  return assets;
}

/**
 * Compare semver strings. Returns true if a >= b.
 */
function semverGte(a, b) {
  const parse = (s) =>
    String(s)
      .replace(/^v/, "")
      .split(".")
      .map((x) => parseInt(x, 10) || 0);
  const [aMaj, aMin, aPatch] = parse(a);
  const [bMaj, bMin, bPatch] = parse(b);
  if (aMaj !== bMaj) return aMaj > bMaj;
  if (aMin !== bMin) return aMin > bMin;
  return aPatch >= bPatch;
}

// ---------------------------------------------------------------------------
// Handler: GET /api/version
// ---------------------------------------------------------------------------

async function handleVersion(request, env, ctx) {
  const ip = getClientIP(request);
  const rl = await checkRateLimit(env, "version", ip);
  if (rl.limited) return rl.response;

  // Check CF cache first
  const cache = caches.default;
  const cacheKey = new Request(
    `https://of-cache.internal/version/${env.GITHUB_OWNER}/${env.GITHUB_REPO}`,
    { method: "GET" }
  );

  const cached = await cache.match(cacheKey);
  if (cached) {
    // Clone and re-add CORS headers
    const body = await cached.json();
    return corsJSON(body, 200);
  }

  // Fetch fresh from GitHub
  let release;
  try {
    release = await fetchLatestRelease(env);
  } catch (err) {
    return corsJSON({ error: "Failed to fetch release info", detail: err.message }, 502);
  }

  const workerBaseUrl = new URL(request.url).origin;
  const tag = release.tag_name || "";
  const version = tag.replace(/^v/, "");
  const minRequired = env.MIN_REQUIRED_VERSION || "0.0.0";
  const forceUpdate = !semverGte(version, minRequired);

  const payload = {
    latest: version,
    tag,
    min_required: minRequired,
    force_update: forceUpdate,
    release_notes_en: extractBlock(release.body, "en"),
    release_notes_zh: extractBlock(release.body, "zh"),
    assets: buildAssetsMap(release, workerBaseUrl),
    published_at: release.published_at || null,
  };

  // Store in CF cache for VERSION_CACHE_TTL seconds
  const cacheResponse = new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": `public, max-age=${VERSION_CACHE_TTL}`,
    },
  });
  ctx.waitUntil(cache.put(cacheKey, cacheResponse));

  return corsJSON(payload, 200);
}

// ---------------------------------------------------------------------------
// Handler: GET /dl/latest/:asset  and  GET /dl/:tag/:asset
// ---------------------------------------------------------------------------

async function handleDownload(request, env, ctx, tag, assetName) {
  const ip = getClientIP(request);
  const rl = await checkRateLimit(env, "download", ip);
  if (rl.limited) return rl.response;

  // Resolve tag if "latest"
  let resolvedTag = tag;
  if (!resolvedTag) {
    try {
      const release = await fetchLatestRelease(env);
      resolvedTag = release.tag_name;
    } catch (err) {
      return corsJSON({ error: "Failed to resolve latest tag", detail: err.message }, 502);
    }
  }

  // Build the GitHub download URL
  const ghDownloadUrl = `https://github.com/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/releases/download/${encodeURIComponent(resolvedTag)}/${encodeURIComponent(assetName)}`;

  // Forward Range header for resume support
  const fetchHeaders = new Headers({ "User-Agent": "OpenFrequency-Workers/1.0" });
  const rangeHeader = request.headers.get("Range");
  if (rangeHeader) fetchHeaders.set("Range", rangeHeader);
  if (env.GITHUB_TOKEN) fetchHeaders.set("Authorization", `Bearer ${env.GITHUB_TOKEN}`);

  let ghResponse;
  try {
    ghResponse = await fetch(ghDownloadUrl, { headers: fetchHeaders });
  } catch (err) {
    return corsJSON({ error: "Failed to connect to GitHub", detail: err.message }, 502);
  }

  if (!ghResponse.ok && ghResponse.status !== 206) {
    return corsJSON(
      { error: `GitHub returned ${ghResponse.status} for asset "${assetName}"` },
      ghResponse.status === 404 ? 404 : 502
    );
  }

  // Build response headers — forward relevant ones from GitHub
  const responseHeaders = {
    ...CORS_HEADERS,
    "Cache-Control": "public, max-age=86400",
  };

  const forward = [
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "ETag",
    "Last-Modified",
  ];
  for (const h of forward) {
    const val = ghResponse.headers.get(h);
    if (val) responseHeaders[h] = val;
  }

  // Content-Disposition: suggest filename
  responseHeaders["Content-Disposition"] = `attachment; filename="${assetName}"`;

  // Stream the body directly — no buffering
  return new Response(ghResponse.body, {
    status: ghResponse.status,
    headers: responseHeaders,
  });
}

// ---------------------------------------------------------------------------
// Handler: POST /api/crash
// ---------------------------------------------------------------------------

const CRASH_ALLOWED_FIELDS = new Set([
  "crash_id",
  "app_version",
  "os",
  "os_version",
  "arch",
  "python_version",
  "exception_type",
  "exception_message",
  "traceback",
  "extra",
  "timestamp",
  "locale",
  "sim_type",
]);

const CRASH_MAX_BODY = 65536; // 64 KB

async function handleCrash(request, env, ctx) {
  const ip = getClientIP(request);

  // Body size guard via Content-Length before reading
  const contentLength = parseInt(request.headers.get("Content-Length") || "0", 10);
  if (contentLength > CRASH_MAX_BODY) {
    return corsJSON({ error: "Payload too large", max_bytes: CRASH_MAX_BODY }, 413);
  }

  let body;
  try {
    const raw = await request.text();
    if (raw.length > CRASH_MAX_BODY) {
      return corsJSON({ error: "Payload too large", max_bytes: CRASH_MAX_BODY }, 413);
    }
    body = JSON.parse(raw);
  } catch {
    return corsJSON({ error: "Invalid JSON body" }, 400);
  }

  // Validate crash_id
  const crashId = sanitizeString(body.crash_id, 128);
  if (!crashId) {
    return corsJSON({ error: "crash_id is required" }, 400);
  }

  // Rate limit check (after reading body so we have crash_id)
  const rl = await checkRateLimit(env, "crash", ip);
  if (rl.limited) return rl.response;

  const kvKey = `crash:${crashId}`;

  // Dedup: if crash_id already exists, silently accept
  const existing = await env.OF_KV.get(kvKey, { type: "json" });
  if (existing) {
    return corsJSON({ status: "accepted", crash_id: crashId, deduplicated: true }, 201);
  }

  // Whitelist and sanitize fields
  const record = {
    crash_id:          crashId,
    app_version:       sanitizeString(body.app_version, 32),
    os:                sanitizeEnum(body.os, ["windows", "linux", "darwin", "unknown"], "unknown"),
    os_version:        sanitizeString(body.os_version, 128),
    arch:              sanitizeEnum(body.arch, ["x86_64", "arm64", "x86", "unknown"], "unknown"),
    python_version:    sanitizeString(body.python_version, 32),
    exception_type:    sanitizeString(body.exception_type, 256),
    exception_message: sanitizeString(body.exception_message, 1024),
    traceback:         sanitizeString(body.traceback, 32768),
    extra:             sanitizeObject(body.extra, 4096),
    timestamp:         sanitizeString(body.timestamp, 64),
    locale:            sanitizeString(body.locale, 32),
    sim_type:          sanitizeEnum(body.sim_type, ["xplane", "msfs", "p3d", "fsx", "unknown"], "unknown"),
    _received_at:      new Date().toISOString(),
    _ip_hash:          await hashIP(ip),
  };

  // Check global quota (crash: silent accept, flag in response)
  const globalQuota = rl.globalQuota;

  await env.OF_KV.put(kvKey, JSON.stringify(record), { expirationTtl: KV_TTL.crash });

  return corsJSON(
    {
      status: "accepted",
      crash_id: crashId,
      deduplicated: false,
      ...(globalQuota ? { note: "global_quota_reached" } : {}),
    },
    201
  );
}

// ---------------------------------------------------------------------------
// Handler: POST /api/feedback
// ---------------------------------------------------------------------------

const FEEDBACK_MAX_BODY = 524288; // 512 KB

async function handleFeedback(request, env, ctx) {
  const ip = getClientIP(request);

  const contentLength = parseInt(request.headers.get("Content-Length") || "0", 10);
  if (contentLength > FEEDBACK_MAX_BODY) {
    return corsJSON({ error: "Payload too large", max_bytes: FEEDBACK_MAX_BODY }, 413);
  }

  let body;
  try {
    const raw = await request.text();
    if (raw.length > FEEDBACK_MAX_BODY) {
      return corsJSON({ error: "Payload too large", max_bytes: FEEDBACK_MAX_BODY }, 413);
    }
    body = JSON.parse(raw);
  } catch {
    return corsJSON({ error: "Invalid JSON body" }, 400);
  }

  const rl = await checkRateLimit(env, "feedback", ip);
  if (rl.limited) return rl.response;

  // Generate a unique feedback ID
  const feedbackId = await generateId("fb");

  const record = {
    feedback_id:  feedbackId,
    app_version:  sanitizeString(body.app_version, 32),
    category:     sanitizeEnum(
      body.category,
      ["bug", "feature", "ux", "performance", "content", "other"],
      "other"
    ),
    rating:       sanitizeRating(body.rating),
    subject:      sanitizeString(body.subject, 256),
    message:      sanitizeString(body.message, 65536),
    contact:      sanitizeString(body.contact, 256),
    locale:       sanitizeString(body.locale, 32),
    os:           sanitizeEnum(body.os, ["windows", "linux", "darwin", "unknown"], "unknown"),
    sim_type:     sanitizeEnum(body.sim_type, ["xplane", "msfs", "p3d", "fsx", "unknown"], "unknown"),
    extra:        sanitizeObject(body.extra, 8192),
    _received_at: new Date().toISOString(),
    _ip_hash:     await hashIP(ip),
  };

  const kvKey = `feedback:${feedbackId}`;
  await env.OF_KV.put(kvKey, JSON.stringify(record), { expirationTtl: KV_TTL.feedback });

  return corsJSON({ status: "accepted", feedback_id: feedbackId }, 201);
}

// ---------------------------------------------------------------------------
// Sanitization helpers
// ---------------------------------------------------------------------------

function sanitizeString(val, maxLen) {
  if (typeof val !== "string") return null;
  return val.slice(0, maxLen);
}

function sanitizeEnum(val, allowed, fallback) {
  if (typeof val === "string" && allowed.includes(val)) return val;
  return fallback;
}

function sanitizeObject(val, maxJsonLen) {
  if (val === null || val === undefined) return null;
  if (typeof val !== "object" || Array.isArray(val)) return null;
  try {
    const s = JSON.stringify(val);
    if (s.length > maxJsonLen) return { _truncated: true };
    return val;
  } catch {
    return null;
  }
}

function sanitizeRating(val) {
  const n = Number(val);
  if (!Number.isFinite(n)) return null;
  if (n < 1 || n > 5) return null;
  return Math.round(n);
}

// ---------------------------------------------------------------------------
// Crypto helpers
// ---------------------------------------------------------------------------

async function hashIP(ip) {
  const encoder = new TextEncoder();
  const data = encoder.encode("of-salt-2024:" + ip);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

async function generateId(prefix) {
  const arr = new Uint8Array(12);
  crypto.getRandomValues(arr);
  const hex = Array.from(arr)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `${prefix}_${hex}_${Date.now().toString(36)}`;
}

// ---------------------------------------------------------------------------
// Response helpers
// ---------------------------------------------------------------------------

function corsJSON(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...CORS_HEADERS,
      "Content-Type": "application/json",
    },
  });
}
