/**
 * lee.vino9.net path router — one Worker, many apps.
 *
 * Fronts several independent backends (Cloud Run services, etc.) under a single
 * hostname by context path, so you need only ONE DNS record, ONE cert, and ONE
 * Worker for the whole fleet. Add an app = add a line to ROUTES.
 *
 *   lee.vino9.net/trackme/...  ->  https://trackme-xxxx.run.app/...
 *   lee.vino9.net/budget/...   ->  https://budget-yyyy.run.app/...
 *
 * Per request the Worker:
 *   1. longest-prefix-matches the path against ROUTES,
 *   2. strips the prefix from the forwarded path ("/trackme/t/x" -> "/t/x"),
 *   3. sets X-Forwarded-Prefix so the backend can build correct subpath URLs,
 *   4. proxies to the backend, preserving method/body/headers (incl. the
 *      Cf-Access-Jwt-Assertion that Cloudflare Access injects).
 *
 * Zero Trust is enforced by Cloudflare Access BEFORE this Worker runs — define a
 * separate self-hosted Access application per path (see README.md). The Worker
 * does no auth itself; it is only the router.
 *
 * SECURITY: backends stay publicly reachable at their own origin URL (Cloud Run
 * "allow unauthenticated"). Access only guards the lee.vino9.net edge, so each
 * backend MUST also validate the Access JWT itself (track_me does this in
 * viewer/auth.py once CF_ACCESS_AUD is set to the real AUD — not the "ignore"
 * sentinel). Optionally uncomment the shared-secret header below for defense in
 * depth and have each backend reject requests that lack it.
 */

const ROUTES = {
  "/trackme": "https://track-me-950978288232.us-west1.run.app",
  // "/budget":  "https://budget-yyyy.run.app",
  // "/notes":   "https://notes-zzzz.run.app",
};

// const ORIGIN_SECRET = "change-me"; // defense-in-depth; must match the backend

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Longest-prefix match so "/trackme" wins over a shorter "/t".
    const prefix = Object.keys(ROUTES)
      .filter((p) => url.pathname === p || url.pathname.startsWith(p + "/"))
      .sort((a, b) => b.length - a.length)[0];

    if (!prefix) {
      return new Response("Not found", { status: 404 });
    }

    const target = new URL(ROUTES[prefix]);
    target.pathname = url.pathname.slice(prefix.length) || "/";
    target.search = url.search;

    const headers = new Headers(request.headers);
    headers.set("X-Forwarded-Prefix", prefix);
    headers.set("Host", target.host);
    // headers.set("X-Origin-Secret", ORIGIN_SECRET);

    return fetch(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual", // let the browser see the backend's redirects verbatim
    });
  },
};
