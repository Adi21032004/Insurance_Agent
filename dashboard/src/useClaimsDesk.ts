import { useCallback, useEffect, useState } from "react";
import {
  getClaim,
  getClaims,
  getCurrentRun,
  getInstructions,
  getRun,
  recordPayment,
  signOffClaim,
  startClaim,
  startQueueRun,
  type AgentRun,
  type ClaimDetail,
  type ClaimSummary,
} from "./api";

export type DeskView = "queue" | "instructions";

interface Instructions {
  queue_instructions: string;
  claim_instructions: string;
}

function useClaimPolling(
  selectedClaimId: string | null,
  shouldPollClaim: boolean,
  setDetail: (detail: ClaimDetail) => void,
  setClaims: (claims: ClaimSummary[]) => void,
  setError: (error: string) => void,
) {
  useEffect(() => {
    if (selectedClaimId === null) return;
    const activity = { current: true };
    const isCurrent = () => activity.current;
    const load = async () => {
      try {
        const nextDetail = await getClaim(selectedClaimId);
        if (!isCurrent()) return;
        setDetail(nextDetail);
        const nextQueue = await getClaims();
        if (!isCurrent()) return;
        setClaims(nextQueue.claims);
      } catch (reason: unknown) {
        if (isCurrent()) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the claim.",
          );
        }
      }
    };
    void load();
    const timer = shouldPollClaim
      ? window.setInterval(() => {
          void load();
        }, 1200)
      : null;
    return () => {
      activity.current = false;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [selectedClaimId, setClaims, setDetail, setError, shouldPollClaim]);
}

function useRunPolling(
  run: AgentRun | null,
  refreshRun: (knownRunId?: string | null) => Promise<AgentRun | null>,
  setError: (error: string) => void,
) {
  useEffect(() => {
    if (run?.status !== "in progress") return;
    const timer = window.setInterval(() => {
      void refreshRun(run.run_id).catch((reason: unknown) => {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not refresh the run.",
        );
      });
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [refreshRun, run, setError]);
}

export function useClaimsDesk() {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [instructions, setInstructions] = useState<Instructions | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [trackedRunId, setTrackedRunId] = useState<string | null>(null);
  const [maxClaims, setMaxClaims] = useState(5);
  const [view, setView] = useState<DeskView>("queue");
  const [error, setError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [selectedHandler, setSelectedHandler] = useState("HND-01");

  const refreshClaim = useCallback(async (claimId: string) => {
    const nextDetail = await getClaim(claimId);
    setDetail(nextDetail);
    const nextQueue = await getClaims();
    setClaims(nextQueue.claims);
  }, []);

  const refreshRun = useCallback(
    async (knownRunId: string | null = trackedRunId) => {
      const current = await getCurrentRun();
      if (current.run !== null) {
        setRun(current.run);
        setTrackedRunId(current.run.run_id);
        const queue = await getClaims();
        setClaims(queue.claims);
        return current.run;
      }
      if (knownRunId !== null) {
        const saved = await getRun(knownRunId);
        setRun(saved);
        setTrackedRunId(knownRunId);
        const queue = await getClaims();
        setClaims(queue.claims);
        return saved;
      }
      setRun(null);
      return null;
    },
    [trackedRunId],
  );

  useEffect(() => {
    let active = true;
    void Promise.all([getClaims(), getCurrentRun()])
      .then(([queue, current]) => {
        if (!active) return;
        setClaims(queue.claims);
        if (queue.claims.length > 0)
          setSelectedClaimId(queue.claims[0].claim_id);
        if (current.run !== null) {
          setRun(current.run);
          setTrackedRunId(current.run.run_id);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the queue.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const shouldPollClaim =
    selectedClaimId !== null &&
    detail !== null &&
    detail.claim.claim_id === selectedClaimId &&
    detail.status === "in progress";

  useClaimPolling(
    selectedClaimId,
    shouldPollClaim,
    setDetail,
    setClaims,
    setError,
  );
  useRunPolling(run, refreshRun, setError);

  const runClaimAction = useCallback(
    async (claimId: string, action: () => Promise<unknown>) => {
      setActionBusy(true);
      setError("");
      try {
        await action();
        await refreshClaim(claimId);
      } catch (reason: unknown) {
        setError(
          reason instanceof Error
            ? reason.message
            : "The action could not be completed.",
        );
      } finally {
        setActionBusy(false);
      }
    },
    [refreshClaim],
  );

  const handleRun = useCallback(() => {
    if (selectedClaimId === null) return;
    setActionBusy(true);
    setError("");
    void startClaim(selectedClaimId)
      .then(async (started) => {
        setTrackedRunId(started.run_id);
        await Promise.all([
          refreshClaim(selectedClaimId),
          refreshRun(started.run_id),
        ]);
      })
      .catch((reason: unknown) => {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not start the claim review.",
        );
      })
      .finally(() => {
        setActionBusy(false);
      });
  }, [refreshClaim, refreshRun, selectedClaimId]);

  const handleQueueRun = useCallback(() => {
    if (maxClaims < 1 || maxClaims > 25 || run?.status === "in progress")
      return;
    setActionBusy(true);
    setError("");
    void startQueueRun(maxClaims)
      .then(async (started) => {
        setTrackedRunId(started.run_id);
        const updates: Promise<unknown>[] = [refreshRun(started.run_id)];
        if (selectedClaimId !== null)
          updates.push(refreshClaim(selectedClaimId));
        await Promise.all(updates);
      })
      .catch((reason: unknown) => {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not start the queue run.",
        );
      })
      .finally(() => {
        setActionBusy(false);
      });
  }, [maxClaims, refreshClaim, refreshRun, run?.status, selectedClaimId]);

  const handleSignOff = useCallback(() => {
    if (selectedClaimId === null) return;
    void runClaimAction(selectedClaimId, () =>
      signOffClaim(selectedClaimId, selectedHandler),
    );
  }, [runClaimAction, selectedClaimId, selectedHandler]);

  const handlePay = useCallback(() => {
    const payout = detail?.decision?.payout_inr;
    if (selectedClaimId === null || payout === undefined) return;
    void runClaimAction(selectedClaimId, () =>
      recordPayment(selectedClaimId, payout),
    );
  }, [detail, runClaimAction, selectedClaimId]);

  const openInstructions = useCallback(async () => {
    setView("instructions");
    setError("");
    try {
      setInstructions(await getInstructions());
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load the instructions.",
      );
    }
  }, []);

  return {
    actionBusy,
    claims,
    detail,
    error,
    handlePay,
    handleQueueRun,
    handleRun,
    handleSignOff,
    instructions,
    maxClaims,
    openInstructions,
    run,
    selectedClaimId,
    selectedHandler,
    setMaxClaims,
    setSelectedHandler,
    setSelectedClaimId,
    setView,
    view,
  };
}
