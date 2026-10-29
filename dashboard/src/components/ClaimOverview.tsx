import { formatInr, formatTime, type ClaimDetail } from "../api";
import { StatusPill } from "./ClaimQueue";

type ClaimOverviewProps = Readonly<{
  detail: ClaimDetail;
  actionBusy: boolean;
  selectedHandler: string;
  onHandlerChange: (handlerId: string) => void;
  onRun: () => void;
  onSignOff: () => void;
}>;

export function ClaimOverview({
  detail,
  actionBusy,
  selectedHandler,
  onHandlerChange,
  onRun,
  onSignOff,
}: ClaimOverviewProps) {
  const claim = detail.claim;
  const signedOffBy = detail.decision?.signed_off_by;
  const signedOffAt = detail.sign_off_at;
  let workMessage = "The UnderCover Agent is reviewing this claim.";
  if (detail.decision?.outcome === "approve") {
    workMessage = signedOffBy
      ? `Signed off by ${signedOffBy} at ${formatTime(signedOffAt)}. Payment is processing.`
      : "Waiting for the agent to pay it.";
  }

  return (
    <section className="claim-overview card">
      <div className="claim-heading">
        <div>
          <p className="eyebrow">{claim.claim_type.replaceAll("_", " ")}</p>
          <h2>{claim.summary}</h2>
          <p className="claim-id">{claim.claim_id}</p>
        </div>
        <StatusPill status={detail.status} large />
      </div>
      <div className="claim-facts">
        <div>
          <span className="fact-label">Claimed</span>
          <strong className="money">
            {formatInr(claim.amount_claimed_inr)}
          </strong>
        </div>
        <div>
          <span className="fact-label">Incident</span>
          <strong>{claim.incident_date}</strong>
        </div>
        <div>
          <span className="fact-label">Location</span>
          <strong>{claim.incident_location}</strong>
        </div>
        <div>
          <span className="fact-label">Submitted</span>
          <strong>{claim.submitted_on}</strong>
        </div>
      </div>
      <div className="action-panel">
        {detail.status === "waiting" && (
          <button
            className="button button-primary"
            disabled={actionBusy}
            onClick={onRun}
          >
            {actionBusy ? "Starting review…" : "Start agent review"}
          </button>
        )}
        {detail.status === "in progress" && (
          <p className="working-message" aria-live="polite">
            {workMessage}
          </p>
        )}
        {detail.status === "waiting for sign-off" && (
          <div className="signoff-controls">
            <p>
              The approved payout is over{" "}
              {formatInr(detail.company.payout_approval_limit_inr)}. A handler
              must sign off before payment.
            </p>
            <label>
              Handler
              <select
                value={selectedHandler}
                onChange={(event) => {
                  onHandlerChange(event.target.value);
                }}
              >
                {detail.handlers.map((handler) => (
                  <option key={handler.handler_id} value={handler.handler_id}>
                    {handler.name} · {handler.role}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button button-secondary"
              disabled={actionBusy}
              onClick={onSignOff}
            >
              Sign off payout
            </button>
          </div>
        )}
        {detail.payment !== null && (
          <p className="paid-message">
            Paid {formatInr(detail.payment.amount_inr)}
            {detail.payment.signed_off_by && (
              <>
                , signed off by {detail.payment.signed_off_by} at{" "}
                {formatTime(detail.payment.signed_off_at)}
              </>
            )}
          </p>
        )}
        {detail.sign_off_handler_id !== null && signedOffBy === undefined && (
          <p className="signoff-message">
            Signed off by {detail.sign_off_handler_id}
          </p>
        )}
      </div>
    </section>
  );
}
