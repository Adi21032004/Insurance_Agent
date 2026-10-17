import { formatInr, type ClaimDetail } from "../api";
import { outcomeLabel } from "./labels";

export function EvidenceCard({ detail }: Readonly<{ detail: ClaimDetail }>) {
  const claim = detail.claim;
  return (
    <section className="card content-card">
      <SectionHeading eyebrow="Claim file" title="What happened" />
      <p className="story-copy">{claim.story}</p>
      <h4>Items and expenses</h4>
      <ul className="item-list">
        {claim.items.map((item, index) => (
          <li key={`${item.description}-${index.toString()}`}>
            <span>
              <strong>{item.description}</strong>
              <small>{item.category.replaceAll("_", " ")}</small>
            </span>
            <strong className="money">{formatInr(item.amount_inr)}</strong>
          </li>
        ))}
      </ul>
      <h4>Supporting documents</h4>
      <div className="document-list">
        {claim.documents.map((document) => (
          <details className="document-entry" key={document.name}>
            <summary>
              <span>{document.name}</span>
              <small>{document.type.replaceAll("_", " ")}</small>
            </summary>
            <p>{document.content}</p>
          </details>
        ))}
      </div>
    </section>
  );
}

export function PolicyCard({ detail }: Readonly<{ detail: ClaimDetail }>) {
  return (
    <section className="card content-card">
      <SectionHeading eyebrow="Policy" title={detail.plan.name} />
      <div className="policy-facts">
        <PolicyFact label="Trip" value={detail.policy.trip.destination} />
        <PolicyFact
          label="Dates"
          value={`${detail.policy.trip.departs_on} to ${detail.policy.trip.returns_on}`}
        />
        <PolicyFact
          label="Trip cost"
          value={formatInr(detail.policy.trip.trip_cost_inr)}
        />
        <PolicyFact
          label="Travelers"
          value={detail.policy.travelers.join(", ")}
        />
      </div>
      <p className="addon-list">
        Add-ons:{" "}
        {detail.policy.add_ons.length
          ? detail.policy.add_ons.join(", ")
          : "None"}
      </p>
      <h4>Plan limits</h4>
      <ul className="limit-list">
        <LimitRow
          label="Trip cancellation and interruption"
          amount={detail.plan.trip_cancellation_max_inr}
        />
        <LimitRow
          label={`Trip delay · ${detail.plan.trip_delay_waiting_hours.toString()} hour wait`}
          amount={detail.plan.trip_delay_max_inr}
        />
        <LimitRow
          label={`Baggage delay · ${detail.plan.baggage_delay_waiting_hours.toString()} hour wait`}
          amount={detail.plan.baggage_delay_max_inr}
        />
        <LimitRow
          label={`Baggage · ${formatInr(detail.plan.per_item_limit_inr)} per item`}
          amount={detail.plan.baggage_max_inr}
        />
        <LimitRow
          label="Passport and travel documents"
          amount={detail.plan.passport_max_inr}
        />
        <LimitRow
          label="Emergency medical"
          amount={detail.plan.emergency_medical_max_inr}
        />
        <LimitRow label="Deductible" amount={detail.plan.deductible_inr} />
      </ul>
      <details className="policy-wording">
        <summary>Read policy wording</summary>
        <pre>{detail.policy_wording}</pre>
      </details>
    </section>
  );
}

export function DecisionCard({ detail }: Readonly<{ detail: ClaimDetail }>) {
  const decision = detail.decision;
  return (
    <section className="card content-card decision-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">UnderCover Agent</p>
          <h3>Decision</h3>
        </div>
        {decision !== null && (
          <span className={`outcome-pill outcome-${decision.outcome}`}>
            {outcomeLabel(decision.outcome)}
          </span>
        )}
      </div>
      {decision === null ? (
        <p className="muted-copy">The agent has not recorded a decision.</p>
      ) : (
        <DecisionDetails detail={detail} />
      )}
    </section>
  );
}

function DecisionDetails({ detail }: Readonly<{ detail: ClaimDetail }>) {
  const decision = detail.decision;
  if (decision === null) return null;
  return (
    <>
      <p className="decision-reason">{decision.reason}</p>
      <p className="policy-citation">{decision.policy_section}</p>
      {decision.outcome === "approve" && (
        <div className="payout-box">
          <span>Proposed payout</span>
          <strong className="money">{formatInr(decision.payout_inr)}</strong>
          {decision.needs_sign_off && (
            <small>Handler sign-off required before payment</small>
          )}
        </div>
      )}
      {decision.calculation.length > 0 && (
        <ul className="calculation-list" aria-label="Payout calculation">
          {decision.calculation.map((step, index) => (
            <li key={`${step.step}-${index.toString()}`}>
              <span>{step.step}</span>
              <span className="money">{formatInr(step.amount_inr)}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function ActivityCard({ detail }: Readonly<{ detail: ClaimDetail }>) {
  return (
    <section className="card content-card">
      <SectionHeading eyebrow="Activity" title="Review steps" />
      {detail.progress.length === 0 ? (
        <p className="muted-copy">No review steps yet.</p>
      ) : (
        <ol className="timeline">
          {detail.progress.map((event, index) => (
            <li key={`${event.created_at}-${index.toString()}`}>
              <span className="timeline-marker">{index + 1}</span>
              <div>
                <strong>{event.step}</strong>
                <p>{event.detail}</p>
                <small className="event-time">{event.created_at}</small>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function CustomerCard({ detail }: Readonly<{ detail: ClaimDetail }>) {
  const customer = detail.customer;
  return (
    <section className="card content-card">
      <SectionHeading eyebrow="Customer" title={customer.name} />
      <p className="contact-line">{customer.email}</p>
      <p className="contact-line">{customer.phone}</p>
      <p className="contact-line">
        {customer.address.street}, {customer.address.city},{" "}
        {customer.address.state} {customer.address.zip}
      </p>
      <h4>Claim history</h4>
      {customer.previous_claims.length === 0 ? (
        <p className="muted-copy">No previous claims.</p>
      ) : (
        <ul className="history-list">
          {customer.previous_claims.map((previous) => (
            <li key={previous.claim_id}>
              <span>
                <strong>{previous.claim_id}</strong>
                <small>{previous.summary}</small>
              </span>
              <span className="history-outcome">{previous.outcome}</span>
              <span className="money">
                {formatInr(previous.amount_paid_inr)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SectionHeading({
  eyebrow,
  title,
}: Readonly<{ eyebrow: string; title: string }>) {
  return (
    <div className="section-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h3>{title}</h3>
      </div>
    </div>
  );
}

function PolicyFact({
  label,
  value,
}: Readonly<{ label: string; value: string }>) {
  return (
    <p>
      <span>{label}</span>
      <strong>{value}</strong>
    </p>
  );
}

function LimitRow({
  label,
  amount,
}: Readonly<{ label: string; amount: number }>) {
  return (
    <li>
      <span>{label}</span>
      <strong className="money">{formatInr(amount)}</strong>
    </li>
  );
}
