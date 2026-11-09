import { createServer, request as httpRequest } from "node:http";

const port = Number(process.env.AGENT_STAND_IN_PORT ?? "8003");
const upstreamPort = Number(process.env.AGENT_STAND_IN_UPSTREAM_PORT ?? "8002");
const resumed: string[] = [];
const started: { path: string; body: Record<string, unknown> }[] = [];

const server = createServer((incoming, outgoing) => {
  const url = new URL(incoming.url ?? "/", "http://127.0.0.1");
  if (incoming.method === "GET" && url.pathname === "/api/health") {
    outgoing.writeHead(200, { "Content-Type": "application/json" });
    outgoing.end(JSON.stringify({ status: "ok" }));
    return;
  }
  if (incoming.method === "GET" && url.pathname === "/api/resumed") {
    outgoing.writeHead(200, { "Content-Type": "application/json" });
    outgoing.end(JSON.stringify({ claim_ids: resumed }));
    return;
  }
  if (incoming.method === "GET" && url.pathname === "/api/started") {
    outgoing.writeHead(200, { "Content-Type": "application/json" });
    outgoing.end(JSON.stringify({ requests: started }));
    return;
  }
  if (
    incoming.method === "POST" &&
    (url.pathname === "/api/dispatches" || url.pathname === "/api/evaluations")
  ) {
    let body = "";
    incoming.setEncoding("utf8");
    incoming.on("data", (chunk: string) => {
      body += chunk;
    });
    incoming.on("end", () => {
      started.push({
        path: url.pathname,
        body: JSON.parse(body) as Record<string, unknown>,
      });
      outgoing.writeHead(202, { "Content-Type": "application/json" });
      outgoing.end(JSON.stringify({ status: "accepted" }));
    });
    return;
  }
  const resume = /^\/api\/claims\/([^/]+)\/resume$/.exec(url.pathname);
  if (incoming.method === "POST" && resume !== null) {
    resumed.push(decodeURIComponent(resume[1]));
    outgoing.writeHead(202, { "Content-Type": "application/json" });
    outgoing.end(JSON.stringify({ status: "accepted" }));
    return;
  }
  const claimRun = /^\/api\/claims\/([^/]+)\/run$/.exec(url.pathname);
  if (incoming.method === "POST" && claimRun !== null) {
    const upstream = httpRequest(
      {
        hostname: "127.0.0.1",
        port: upstreamPort,
        path: `/api/claims/${encodeURIComponent(claimRun[1])}/run`,
        method: "POST",
        headers: incoming.headers,
      },
      (response) => {
        outgoing.writeHead(response.statusCode ?? 502, response.headers);
        response.pipe(outgoing);
      },
    );
    upstream.on("error", () => {
      outgoing.writeHead(502, { "Content-Type": "application/json" });
      outgoing.end(JSON.stringify({ detail: "Agent service is unavailable" }));
    });
    incoming.pipe(upstream);
    return;
  }
  outgoing.writeHead(404, { "Content-Type": "application/json" });
  outgoing.end(JSON.stringify({ detail: "Not found" }));
});

server.listen(port, "127.0.0.1");
