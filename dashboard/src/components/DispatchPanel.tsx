import { useCallback, useEffect, useState } from "react";
import {
  formatInr,
  formatUsd,
  formatDuration,
  getLatestDispatch,
  startDispatch,
  type DispatchRun,
} from "../api";

type DispatchPanelProps = Readonly<{
  onSelectClaim: (claimId: string) => void;
}>;

function summaryLines(run: DispatchRun): string[] {
  const summary = run.summary;
  if (summary === null) return [];
  const lines = [
    `${String(summary.approved)} approved, ${String(summary.declined)} declined, ${String(summary.referred)} referred.`,
  ];
  if (summary.waiting_for_sign_off.length > 0) {
    lines.push(
      `Waiting for sign-off: ${summary.waiting_for_sign_off
        .map(({ claim_id, payout }) => `${claim_id} ${formatInr(payout)}`)
        .join(", ")}`,
    );
  }
  if (summary.left_for_handler.length > 0) {
    lines.push(`Left for a handler: ${summary.left_for_handler.join(", ")}`);
  }
  if (summary.unfinished.length > 0) {
    lines.push(`Left for the next dispatch: ${summary.unfinished.join(", ")}`);
  }
  return lines;
}

export function DispatchPanel({ onSelectClaim }: DispatchPanelProps) {
  const [agents, setAgents] = useState(3);
  const [run, setRun] = useState<DispatchRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lines = run === null ? [] : summaryLines(run);

  const refresh = useCallback(async () => {
    try {
      setRun(await getLatestDispatch());
      setError("");
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load the dispatch.",
      );
    }
  }, []);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => {
      void refresh();
    }, 0);
    const timer = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const handleStart = async () => {
    setBusy(true);
    setError("");
    try {
      await startDispatch(agents);
      await refresh();
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not start the dispatch.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card dispatch-panel" aria-label="Claim dispatch">
      <div className="dispatch-heading">
        <div>
          <p className="eyebrow">Parallel claim review</p>
          <h2>Dispatch</h2>
        </div>
        {run !== null && (
          <span className={`run-state run-state-${run.status}`}>
            {run.status}
          </span>
        )}
      </div>
      <div className="dispatch-controls">
        <label htmlFor="dispatch-agents">Claim agents at once</label>
        <div>
          <input
            id="dispatch-agents"
            type="number"
            min={1}
            max={25}
            value={agents}
            onChange={(event) => {
              setAgents(Number(event.target.value));
            }}
          />
          <button
            className="button button-primary"
            disabled={
              busy || run?.status === "running" || agents < 1 || agents > 25
            }
            onClick={() => {
              void handleStart();
            }}
            type="button"
          >
            {busy ? "Starting…" : "Start a dispatch"}
          </button>
        </div>
      </div>
      {error !== "" && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {run === null ? (
        <p className="empty-state">No dispatch yet.</p>
      ) : (
        <>
          <div className="dispatch-meta">
            <strong>Dispatch #{run.run_order}</strong>
            <span>
              {run.model} · {run.reasoning_effort} reasoning
            </span>
            <span>
              {run.agents} agents ·{" "}
              {new Date(run.created_at.replace(" ", "T")).toLocaleString()}
            </span>
            <span>Model cost {formatUsd(run.model_cost)}</span>
            {run.run_seconds !== null && (
              <span>Elapsed {formatDuration(run.run_seconds)}</span>
            )}
          </div>
          {run.claims.length === 0 ? (
            <p className="muted-copy">
              No claims were selected for this dispatch.
            </p>
          ) : (
            <div className="dispatch-claims">
              {run.claims.map((claim) => (
                <div className="dispatch-claim" key={claim.claim_id}>
                  <button
                    className="text-link"
                    onClick={() => {
                      onSelectClaim(claim.claim_id);
                    }}
                    type="button"
                  >
                    {claim.claim_id}
                  </button>
                  <span>{claim.customer_name}</span>
                  <span>{claim.status}</span>
                  <span>
                    {claim.decision === null
                      ? "No decision"
                      : `${claim.decision.outcome} · ${formatInr(claim.payout_inr ?? 0)}`}
                  </span>
                </div>
              ))}
            </div>
          )}
          {lines.length > 0 && (
            <div className="dispatch-summary">
              {lines.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
