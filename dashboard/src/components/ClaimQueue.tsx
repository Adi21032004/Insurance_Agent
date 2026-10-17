import { formatInr, type ClaimStatus, type ClaimSummary } from "../api";
import { statusClass } from "./labels";

type ClaimQueueProps = Readonly<{
  claims: readonly ClaimSummary[];
  selectedClaimId: string | null;
  onSelect: (claimId: string) => void;
}>;

export function ClaimQueue({
  claims,
  selectedClaimId,
  onSelect,
}: ClaimQueueProps) {
  return (
    <aside className="queue-panel" aria-label="Claims queue">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Claims desk</p>
          <h2>Queue</h2>
        </div>
        <span className="queue-count">{claims.length}</span>
      </div>
      <div className="queue-list">
        {claims.map((claim) => (
          <button
            aria-current={
              selectedClaimId === claim.claim_id ? "true" : undefined
            }
            className={`queue-item ${selectedClaimId === claim.claim_id ? "is-selected" : ""}`}
            key={claim.claim_id}
            onClick={() => {
              onSelect(claim.claim_id);
            }}
            type="button"
          >
            <span className="queue-item-top">
              <span className="claim-id">{claim.claim_id}</span>
              <StatusPill status={claim.status} />
            </span>
            <span className="queue-summary">{claim.summary}</span>
            <span className="queue-item-bottom">
              <span>{claim.claim_type.replaceAll("_", " ")}</span>
              <span className="money">
                {formatInr(claim.amount_claimed_inr)}
              </span>
            </span>
          </button>
        ))}
        {claims.length === 0 && (
          <p className="empty-state">No claims in the queue.</p>
        )}
      </div>
    </aside>
  );
}

export function StatusPill({
  status,
  large = false,
}: Readonly<{ status: ClaimStatus; large?: boolean }>) {
  const sizeClass = large ? "status-large" : "";
  return (
    <span className={`status-pill ${sizeClass} ${statusClass(status)}`}>
      {status}
    </span>
  );
}
