import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  formatDuration,
  formatInr,
  formatPercent,
  formatUsd,
  getEvaluation,
  listEvaluations,
  startEvaluation,
  type EvaluationRun,
} from "../api";

type ScoreboardPageProps = Readonly<{
  runId: string | null;
  onNavigate: (path: string) => void;
  onOpenClaim: (claimId: string) => void;
}>;

const measures = [
  [
    "Decision accuracy",
    (run: EvaluationRun) => formatPercent(run.measures.decision_accuracy),
  ],
  ["Accuracy", (run: EvaluationRun) => formatPercent(run.measures.accuracy)],
  [
    "Mistake cost",
    (run: EvaluationRun) => formatUsd(run.measures.cost_of_mistakes),
  ],
  [
    "Straight-through",
    (run: EvaluationRun) => formatPercent(run.measures.straight_through),
  ],
  [
    "Model cost / claim",
    (run: EvaluationRun) => formatUsd(run.measures.model_cost_per_claim),
  ],
  [
    "Time / claim",
    (run: EvaluationRun) => formatDuration(run.measures.seconds_per_claim),
  ],
] as const;

function useEvaluations(runId: string | null) {
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [evaluation, setEvaluation] = useState<EvaluationRun | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const current = await listEvaluations();
      setEvaluations(current);
      if (runId !== null) {
        setEvaluation(await getEvaluation(runId));
      } else {
        setEvaluation(null);
      }
      setError("");
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load evaluations.",
      );
    }
  }, [runId]);

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

  return { error, evaluation, evaluations, refresh };
}

function EvaluationForm({ onStarted }: Readonly<{ onStarted: () => void }>) {
  const [evaluationSet, setEvaluationSet] =
    useState<EvaluationRun["evaluation_set"]>("evaluation");
  const [tiny, setTiny] = useState(false);
  const [agents, setAgents] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleStart = async () => {
    setBusy(true);
    setError("");
    try {
      await startEvaluation(evaluationSet, tiny, agents);
      onStarted();
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not start the evaluation.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="card evaluation-form"
      onSubmit={(event) => {
        event.preventDefault();
        void handleStart();
      }}
    >
      <label>
        Evaluation set
        <select
          value={evaluationSet}
          onChange={(event) => {
            setEvaluationSet(
              event.target.value as EvaluationRun["evaluation_set"],
            );
          }}
        >
          <option value="evaluation">Evaluation set</option>
          <option value="held_out">Held-out set</option>
        </select>
      </label>
      <label>
        Claims
        <select
          value={tiny ? "tiny" : "all"}
          onChange={(event) => {
            setTiny(event.target.value === "tiny");
          }}
        >
          <option value="all">All 25</option>
          <option value="tiny">The five tiny claims</option>
        </select>
      </label>
      <label>
        Claim agents at once
        <input
          type="number"
          min={1}
          max={25}
          value={agents}
          onChange={(event) => {
            setAgents(Number(event.target.value));
          }}
        />
      </label>
      <button
        className="button button-primary"
        disabled={busy || agents < 1 || agents > 25}
        type="submit"
      >
        {busy ? "Starting…" : "Start an evaluation"}
      </button>
      {error !== "" && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function EvaluationList({
  evaluations,
  onNavigate,
}: Readonly<{
  evaluations: EvaluationRun[];
  onNavigate: (path: string) => void;
}>) {
  return (
    <section className="card evaluation-list">
      <h2>Evaluations</h2>
      {evaluations.length === 0 ? (
        <p className="empty-state">No evaluations yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Evaluation</th>
                <th>Started</th>
                <th>Set</th>
                <th>Agents</th>
                <th>Model</th>
                <th>Status</th>
                {measures.map(([label]) => (
                  <th key={label}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {evaluations.map((run) => (
                <tr key={run.run_id}>
                  <td>
                    <button
                      className="text-link"
                      onClick={() => {
                        onNavigate(`/scoreboard/${run.run_id}`);
                      }}
                      type="button"
                    >
                      #{run.run_order}
                    </button>
                  </td>
                  <td>
                    {new Date(
                      run.created_at.replace(" ", "T"),
                    ).toLocaleString()}
                  </td>
                  <td>
                    {run.evaluation_set === "held_out"
                      ? "Held-out"
                      : "Evaluation"}{" "}
                    · {run.tiny ? "5 claims" : "25 claims"}
                  </td>
                  <td>{run.agents}</td>
                  <td>
                    {run.model} · {run.reasoning_effort}
                  </td>
                  <td>{run.status}</td>
                  {measures.map(([label, value]) => (
                    <td key={label}>{value(run)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function EvaluationDetail({
  run,
  onNavigate,
  onOpenClaim,
}: Readonly<{
  run: EvaluationRun;
  onNavigate: (path: string) => void;
  onOpenClaim: (claimId: string) => void;
}>) {
  return (
    <section className="scoreboard-detail">
      <button
        className="text-link"
        onClick={() => {
          onNavigate("/scoreboard");
        }}
        type="button"
      >
        ← All evaluations
      </button>
      <div className="card evaluation-overview">
        <p className="eyebrow">Evaluation #{run.run_order}</p>
        <h2>
          {run.evaluation_set === "held_out"
            ? "Held-out set"
            : "Evaluation set"}
        </h2>
        <p>
          {run.status} · {run.agents} agents · {run.model} ·{" "}
          {run.reasoning_effort}
        </p>
        <div className="measure-grid">
          {measures.map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <strong>{value(run)}</strong>
            </div>
          ))}
          <div>
            <span>Run time</span>
            <strong>
              {run.measures.run_seconds === null
                ? "—"
                : formatDuration(run.measures.run_seconds)}
            </strong>
          </div>
        </div>
      </div>
      <section className="card evaluation-claims">
        <h2>Claims</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Copy</th>
                <th>Handler</th>
                <th>Agent</th>
                <th>Result</th>
                <th>Mistake cost</th>
                <th>Model cost</th>
                <th>Time</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {run.claims.map((claim) => (
                <tr key={claim.claim_id}>
                  <td>
                    <button
                      className="text-link"
                      onClick={() => {
                        onOpenClaim(claim.claim_id);
                      }}
                      type="button"
                    >
                      {claim.claim_id}
                    </button>
                    <small>Copy of {claim.copy_of}</small>
                  </td>
                  <td>
                    {claim.handler_decision} · {formatInr(claim.handler_payout)}
                  </td>
                  <td>
                    {claim.decision === null
                      ? "No decision"
                      : `${claim.agent_decision} · ${formatInr(claim.agent_payout)}`}
                  </td>
                  <td>
                    <span
                      className={
                        claim.right
                          ? "score-chip is-right"
                          : "score-chip is-miss"
                      }
                    >
                      {claim.right ? "Right" : "Miss"}
                    </span>
                  </td>
                  <td>{formatUsd(claim.mistake_cost)}</td>
                  <td>{formatUsd(claim.model_cost)}</td>
                  <td>{formatDuration(claim.seconds ?? 0)}</td>
                  <td>{claim.reason ?? "No decision recorded."}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

export function ScoreboardPage({
  runId,
  onNavigate,
  onOpenClaim,
}: ScoreboardPageProps) {
  const { error, evaluation, evaluations, refresh } = useEvaluations(runId);
  let content: ReactNode;
  if (runId === null) {
    content = (
      <>
        <div className="page-heading">
          <p className="eyebrow">Agent quality</p>
          <h2>Scoreboard</h2>
        </div>
        <EvaluationForm
          onStarted={() => {
            void refresh();
          }}
        />
        {error !== "" && (
          <p className="error-banner" role="alert">
            {error}
          </p>
        )}
        <EvaluationList evaluations={evaluations} onNavigate={onNavigate} />
      </>
    );
  } else if (evaluation === null) {
    content = <p className="card">Loading evaluation…</p>;
  } else {
    content = (
      <EvaluationDetail
        run={evaluation}
        onNavigate={onNavigate}
        onOpenClaim={onOpenClaim}
      />
    );
  }

  return <main className="scoreboard-page">{content}</main>;
}
