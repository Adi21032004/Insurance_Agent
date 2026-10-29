import type { APIRequestContext } from "@playwright/test";

const portOffset = Number(process.env.UNDERCOVER_E2E_PORT_OFFSET ?? "25000");
export const AGENT_STAND_IN_URL = `http://127.0.0.1:${String(8003 + portOffset)}`;

export async function resumedClaims(
  request: APIRequestContext,
): Promise<string[]> {
  const response = await request.get(`${AGENT_STAND_IN_URL}/api/resumed`);
  return ((await response.json()) as { claim_ids: string[] }).claim_ids;
}
