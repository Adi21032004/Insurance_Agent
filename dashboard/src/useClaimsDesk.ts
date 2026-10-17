import { useCallback, useEffect, useState } from "react";
import {
  getClaim,
  getClaims,
  getInstructions,
  recordPayment,
  signOffClaim,
  startClaim,
  type ClaimDetail,
  type ClaimSummary,
} from "./api";

export type DeskView = "queue" | "instructions";

export function useClaimsDesk() {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [instructions, setInstructions] = useState<string | null>(null);
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
      .then((result) => {
        if (!active) return;
        setClaims(result.claims);
        if (result.claims.length > 0)
          setSelectedClaimId(result.claims[0].claim_id);
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

  const shouldPoll =
    selectedClaimId !== null &&
    detail !== null &&
    detail.claim.claim_id === selectedClaimId &&
    detail.status === "in progress";

  useEffect(() => {
    if (selectedClaimId === null) return;
    const activity: { current: boolean } = { current: true };
    const isActive = () => activity.current;
    const load = async () => {
      try {
        const nextDetail = await getClaim(selectedClaimId);
        if (!isActive()) return;
        setDetail(nextDetail);
        const nextQueue = await getClaims();
        if (isActive()) setClaims(nextQueue.claims);
      } catch (reason: unknown) {
        if (isActive()) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the claim.",
          );
        }
      }
    };
    void load();
    const timer = shouldPoll
      ? window.setInterval(() => void load(), 1200)
      : null;
    return () => {
      activity.current = false;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [selectedClaimId, shouldPoll]);

  const runAction = useCallback(
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
    void runAction(selectedClaimId, () => startClaim(selectedClaimId));
  }, [runAction, selectedClaimId]);

  const handleSignOff = useCallback(() => {
    if (selectedClaimId === null) return;
    void runAction(selectedClaimId, () =>
      signOffClaim(selectedClaimId, selectedHandler),
    );
  }, [runAction, selectedClaimId, selectedHandler]);

  const handlePay = useCallback(() => {
    const payout = detail?.decision?.payout_inr;
    if (selectedClaimId === null || payout === undefined) return;
    void runAction(selectedClaimId, () =>
      recordPayment(selectedClaimId, payout),
    );
  }, [detail, runAction, selectedClaimId]);

  const openInstructions = useCallback(async () => {
    setView("instructions");
    setError("");
    try {
      const result = await getInstructions();
      setInstructions(result.instructions);
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
