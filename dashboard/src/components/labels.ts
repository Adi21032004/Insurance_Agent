import type { ClaimStatus } from "../api";

export function statusClass(status: ClaimStatus): string {
  return `status-${status.replaceAll(" ", "-")}`;
}

export function outcomeLabel(outcome: string): string {
  if (outcome === "approve") return "Approved";
  if (outcome === "decline") return "Declined";
  return "Referred";
}
