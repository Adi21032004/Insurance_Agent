import { createServer, request as httpRequest } from "node:http";

const port = Number(process.env.AGENT_STAND_IN_PORT ?? "8003");
const upstreamPort = Number(process.env.AGENT_STAND_IN_UPSTREAM_PORT ?? "8002");
const resumed: string[] = [];

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
  const resume = /^\/api\/claims\/([^/]+)\/resume$/.exec(url.pathname);
  if (incoming.method === "POST" && resume !== null) {
    resumed.push(decodeURIComponent(resume[1]));
    outgoing.writeHead(202, { "Content-Type": "application/json" });
    outgoing.end(JSON.stringify({ status: "accepted" }));
    return;
  }
  const run = /^\/api\/runs\/([^/]+)\/run$/.exec(url.pathname);
  if (incoming.method === "POST" && run !== null) {
    const upstream = httpRequest(
      {
        hostname: "127.0.0.1",
        port: upstreamPort,
        path: `/api/runs/${encodeURIComponent(run[1])}/run`,
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
