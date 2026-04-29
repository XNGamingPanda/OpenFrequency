export async function onRequestGet(context) {
  const workersUrl = (context.env.WORKERS_URL || "").replace(/\/+$/, "");
  const downloadToken = context.env.WORKERS_DOWNLOAD_TOKEN || context.env.DOWNLOAD_TOKEN || "";

  if (!workersUrl) {
    return Response.json({ error: "Missing WORKERS_URL" }, { status: 500 });
  }
  if (!downloadToken) {
    return Response.json({ error: "Missing WORKERS_DOWNLOAD_TOKEN" }, { status: 500 });
  }

  const upstream = await fetch(`${workersUrl}/pub/dl/latest`, {
    headers: {
      "X-OF-Download-Token": downloadToken,
    },
  });

  const headers = new Headers(upstream.headers);
  headers.set("Cache-Control", "no-store");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}
