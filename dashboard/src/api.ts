export type ClaimStatus =
  "waiting" | "in progress" | "waiting for sign-off" | "decided";

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
  payment: PaymentRecord | null;
  progress: ProgressEvent[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = (await response.json()) as { detail?: string };
    throw new Error(
      body.detail ?? `Request failed with ${response.status.toString()}`,
    );
  }
  return (await response.json()) as T;
}

export function getClaims(): Promise<{ claims: ClaimSummary[] }> {
  return request("/api/claims");
}

export function getClaim(claimId: string): Promise<ClaimDetail> {
  return request(`/api/claims/${claimId}`);
}

export function getInstructions(): Promise<{ instructions: string }> {
  return request("/api/instructions");
}

export function startClaim(claimId: string): Promise<{ status: ClaimStatus }> {
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

export function recordPayment(
  claimId: string,
  amountInr: number,
): Promise<{ status: string; simulated: boolean; amount_inr: number }> {
  return request(`/api/claims/${claimId}/pay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount_inr: amountInr }),
  });
}

export function formatInr(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(amount);
}
