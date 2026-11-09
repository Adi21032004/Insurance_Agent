export type ClaimStatus =
  | "waiting"
  | "in progress"
  | "waiting for sign-off"
  | "decided"
  | "needs handler";

export type DecisionOutcome = "approve" | "decline" | "refer";

export interface CalculationStep {
  step: string;
  amount_inr: number;
}

export interface Decision {
  outcome: DecisionOutcome;
  reason: string;
  policy_section: string;
  calculation: CalculationStep[];
  payout_inr: number;
  needs_sign_off: boolean;
  signed_off_by?: string | null;
  signed_off_at?: string | null;
  manual_version?: string;
  sections_read?: {
    source: string;
    section_id: string;
    heading: string;
    version: string;
  }[];
}

export interface ClaimSummary {
  claim_id: string;
  claim_type: string;
  summary: string;
  amount_claimed_inr: number;
  status: ClaimStatus;
  decision: Decision | null;
}

export interface ClaimItem {
  description: string;
  category: string;
  amount_inr: number;
}

export interface ClaimDocument {
  name: string;
  type: string;
  content: string;
}

export interface ClaimRecord {
  claim_id: string;
  policy_id: string;
  customer_id: string;
  claim_type: string;
  submitted_on: string;
  incident_date: string;
  incident_location: string;
  summary: string;
  story: string;
  items: ClaimItem[];
  amount_claimed_inr: number;
  documents: ClaimDocument[];
}

export interface PolicyRecord {
  policy_id: string;
  plan: string;
  add_ons: string[];
  purchased_on: string;
  trip: {
    destination: string;
    departs_on: string;
    returns_on: string;
    trip_cost_inr: number;
  };
  travelers: string[];
}

export interface PreviousClaim {
  claim_id: string;
  filed_on: string;
  incident_date: string;
  claim_type: string;
  summary: string;
  outcome: string;
  amount_paid_inr: number;
}

export interface CustomerRecord {
  customer_id: string;
  name: string;
  email: string;
  phone: string;
  address: {
    street: string;
    city: string;
    state: string;
    zip: string;
  };
  customer_since: string;
  previous_claims: PreviousClaim[];
}

export interface PlanRecord {
  plan: string;
  name: string;
  trip_cancellation_max_inr: number;
  trip_delay_waiting_hours: number;
  trip_delay_max_inr: number;
  baggage_delay_waiting_hours: number;
  baggage_delay_max_inr: number;
  baggage_max_inr: number;
  per_item_limit_inr: number;
  passport_max_inr: number;
  emergency_medical_max_inr: number;
  deductible_inr: number;
}

export interface HandlerRecord {
  handler_id: string;
  name: string;
  role: string;
}

export interface ProgressEvent {
  step: string;
  detail: string;
  created_at: string;
}

export interface PaymentRecord {
  amount_inr: number;
  simulated: boolean;
  signed_off_by: string | null;
  signed_off_at: string | null;
}

export interface ClaimDetail {
  claim: ClaimRecord;
  policy: PolicyRecord;
  customer: CustomerRecord;
  plan: PlanRecord;
  add_ons: { add_on: string; name: string; effect: string }[];
  company: { payout_approval_limit_inr: number; usd_to_inr_rate: number };
  handlers: HandlerRecord[];
  policy_wording: string;
  status: ClaimStatus;
  decision: Decision | null;
  sign_off_handler_id: string | null;
  sign_off_at: string | null;
  payment: PaymentRecord | null;
  failure_count: number;
  progress: ProgressEvent[];
  source_reads: SourceRead[];
  evaluation_id?: string | null;
  copy_of?: string | null;
}

export interface SourceRead {
  source: "policy_wording" | "claims_manual";
  section_id: string;
  heading: string;
  version: string;
  content: string;
  created_at: string;
}

export interface DispatchSummary {
  approved: number;
  declined: number;
  referred: number;
  waiting_for_sign_off: { claim_id: string; payout: number }[];
  left_for_handler: string[];
  unfinished: string[];
}

export interface DispatchClaim {
  claim_id: string;
  customer_name: string;
  summary: string;
  status: ClaimStatus;
  decision: Decision | null;
  payout_inr: number | null;
  started_at: string | null;
  seconds: number | null;
  model_cost: number;
}

export interface DispatchRun {
  run_id: string;
  run_order: number;
  status: "running" | "finished" | "stopped";
  agents: number;
  model: string;
  reasoning_effort: string;
  created_at: string;
  finished_at: string | null;
  summary: DispatchSummary | null;
  model_cost: number;
  seconds: number;
  run_seconds: number | null;
  claims: DispatchClaim[];
}

export interface EvaluationClaim extends DispatchClaim {
  copy_of: string;
  handler_decision: DecisionOutcome;
  handler_payout: number;
  agent_decision: DecisionOutcome;
  agent_payout: number;
  right: boolean;
  decision_right: boolean;
  mistake_cost: number;
  reason: string | null;
}

export interface EvaluationMeasures {
  decision_accuracy: number;
  accuracy: number;
  cost_of_mistakes: number;
  straight_through: number;
  model_cost_per_claim: number;
  seconds_per_claim: number;
  run_seconds: number | null;
}

export interface EvaluationRun {
  run_id: string;
  run_order: number;
  status: "in progress" | "completed" | "interrupted" | "stopped";
  evaluation_set: "evaluation" | "held_out";
  tiny: boolean;
  agents: number;
  model: string;
  reasoning_effort: string;
  created_at: string;
  finished_at: string | null;
  model_cost: number;
  claims: EvaluationClaim[];
  measures: EvaluationMeasures;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    let message: string | undefined;
    if (typeof detail === "string") {
      message = detail;
    } else if (
      typeof detail === "object" &&
      detail !== null &&
      "message" in detail &&
      typeof detail.message === "string"
    ) {
      message = detail.message;
    }
    throw new Error(
      message ?? `Request failed with ${response.status.toString()}`,
    );
  }
  return (await response.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getClaims(): Promise<{ claims: ClaimSummary[] }> {
  return request("/api/claims");
}

export function getLatestDispatch(): Promise<DispatchRun | null> {
  return request("/api/dispatches/latest");
}

export function startDispatch(
  agents: number,
): Promise<{ status: string; agents: number }> {
  return post("/api/dispatches", { agents });
}

export function listEvaluations(): Promise<EvaluationRun[]> {
  return request("/api/evaluations");
}

export function getEvaluation(runId: string): Promise<EvaluationRun> {
  return request(`/api/evaluations/${runId}`);
}

export function startEvaluation(
  evaluationSet: EvaluationRun["evaluation_set"],
  tiny: boolean,
  agents: number,
): Promise<{ status: string; evaluation_set: string; tiny: boolean }> {
  return post("/api/evaluations", {
    evaluation_set: evaluationSet,
    tiny,
    agents,
  });
}

export function getClaim(claimId: string): Promise<ClaimDetail> {
  return request(`/api/claims/${claimId}`);
}

export function getInstructions(): Promise<{
  claim_instructions: string;
}> {
  return request("/api/instructions");
}

export function startClaim(
  claimId: string,
): Promise<{ status: ClaimStatus; run_id: string }> {
  return request(`/api/claims/${claimId}/run`, { method: "POST" });
}

export function signOffClaim(
  claimId: string,
  handlerId: string,
): Promise<{ status: ClaimStatus; handler_id: string }> {
  return request(`/api/claims/${claimId}/sign-off`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ handler_id: handlerId }),
  });
}

export function formatInr(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(amount);
}

export function formatUsd(amount: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  }).format(amount);
}

export function formatDuration(seconds: number): string {
  const totalSeconds = Math.round(seconds);
  if (totalSeconds < 60) return `${totalSeconds.toString()} s`;
  const minutes = Math.floor(totalSeconds / 60);
  const remaining = totalSeconds % 60;
  return `${minutes.toString()} min ${remaining.toString()} s`;
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100).toString()}%`;
}

export function formatTime(timestamp: string | null | undefined): string {
  if (timestamp === null || timestamp === undefined) return "";
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp.replace(" ", "T")));
}
