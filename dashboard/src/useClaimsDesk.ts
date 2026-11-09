import { useCallback, useEffect, useState } from "react";
import {
  getClaim,
  getClaims,
  getInstructions,
  signOffClaim,
  startClaim,
  type ClaimDetail,
  type ClaimSummary,
} from "./api";

export type DeskView = "queue" | "instructions";

interface Instructions {
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

export function useClaimsDesk() {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [instructions, setInstructions] = useState<Instructions | null>(null);
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

  useEffect(() => {
    let active = true;
    void getClaims()
      .then((queue) => {
        if (!active) return;
        setClaims(queue.claims);
        if (queue.claims.length > 0)
          setSelectedClaimId(queue.claims[0].claim_id);
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
      .then(async () => {
        await refreshClaim(selectedClaimId);
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
  }, [refreshClaim, selectedClaimId]);

  const handleSignOff = useCallback(() => {
    if (selectedClaimId === null) return;
    void runClaimAction(selectedClaimId, () =>
      signOffClaim(selectedClaimId, selectedHandler),
    );
  }, [runClaimAction, selectedClaimId, selectedHandler]);

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
    handleRun,
    handleSignOff,
    instructions,
    openInstructions,
    selectedClaimId,
    selectedHandler,
    setSelectedHandler,
    setSelectedClaimId,
    setView,
    view,
  };
}
