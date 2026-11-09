import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ClaimQueue } from "./components/ClaimQueue";
import { ClaimWorkspace } from "./components/ClaimWorkspace";
import { DispatchPanel } from "./components/DispatchPanel";
import { InstructionsPage } from "./components/InstructionsPage";
import { ScoreboardPage } from "./components/ScoreboardPage";
import "./App.css";
import { useClaimsDesk } from "./useClaimsDesk";

type Desk = ReturnType<typeof useClaimsDesk>;

function CopyBanner({
  copyOf,
  onView,
}: Readonly<{
  copyOf: string | null | undefined;
  onView: () => void;
}>) {
  return (
    <p className="card evaluation-copy-banner">
      A copy of {copyOf} in this evaluation.{" "}
      <button className="text-link" onClick={onView} type="button">
        View evaluation
      </button>
    </p>
  );
}

function DeskContent({
  desk,
  path,
  evaluationId,
  navigate,
  openClaim,
}: Readonly<{
  desk: Desk;
  path: string;
  evaluationId: string | null;
  navigate: (path: string) => void;
  openClaim: (claimId: string) => void;
}>) {
  if (desk.view === "instructions") {
    return <InstructionsPage instructions={desk.instructions} />;
  }
  if (path.startsWith("/scoreboard")) {
    return (
      <ScoreboardPage
        runId={evaluationId}
        onNavigate={navigate}
        onOpenClaim={openClaim}
      />
    );
  }

  let claimContent: ReactNode;
  if (desk.detail === null) {
    claimContent = (
      <div className="card loading-card">
        <p>
          {desk.claims.length === 0
            ? "Loading claims…"
            : "Select a claim to review."}
        </p>
      </div>
    );
  } else {
    const detail = desk.detail;
    claimContent = (
      <>
        {detail.evaluation_id !== null &&
          detail.evaluation_id !== undefined && (
            <CopyBanner
              copyOf={detail.copy_of}
              onView={() => {
                navigate(`/scoreboard/${detail.evaluation_id ?? ""}`);
              }}
            />
          )}
        <ClaimWorkspace
          actionBusy={desk.actionBusy}
          detail={detail}
          onHandlerChange={desk.setSelectedHandler}
          onRun={desk.handleRun}
          onSignOff={desk.handleSignOff}
          selectedHandler={desk.selectedHandler}
        />
      </>
    );
  }

  return (
    <div className="desk-layout">
      <ClaimQueue
        claims={desk.claims}
        selectedClaimId={desk.selectedClaimId}
        onSelect={desk.setSelectedClaimId}
      />
      <section className="detail-panel" aria-label="Claim details">
        <DispatchPanel onSelectClaim={openClaim} />
        {claimContent}
      </section>
    </div>
  );
}

export function App() {
  const desk = useClaimsDesk();
  const { setSelectedClaimId, setView } = desk;
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const handlePopState = () => {
      setPath(window.location.pathname);
      setView("queue");
    };
    window.addEventListener("popstate", handlePopState);
    return () => {
      window.removeEventListener("popstate", handlePopState);
    };
  }, [setView]);
  const navigate = useCallback(
    (nextPath: string) => {
      window.history.pushState(null, "", nextPath);
      setPath(nextPath);
      setView("queue");
    },
    [setView],
  );
  const openClaim = useCallback(
    (claimId: string) => {
      setSelectedClaimId(claimId);
      navigate("/");
    },
    [setSelectedClaimId, navigate],
  );
  const evaluationId = path.startsWith("/scoreboard/")
    ? decodeURIComponent(path.slice("/scoreboard/".length))
    : null;
  const queueActive = path === "/" && desk.view === "queue";
  const scoreboardActive = path.startsWith("/scoreboard");

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            UC
          </span>
          <div>
            <p className="eyebrow">UnderCover Travel Insurance</p>
            <h1>Claims desk</h1>
          </div>
        </div>
        <nav className="top-nav" aria-label="Main navigation">
          <button
            aria-current={queueActive ? "page" : undefined}
            className={queueActive ? "nav-button is-active" : "nav-button"}
            onClick={() => {
              navigate("/");
            }}
            type="button"
          >
            Claims queue
          </button>
          <button
            aria-current={scoreboardActive ? "page" : undefined}
            className={scoreboardActive ? "nav-button is-active" : "nav-button"}
            onClick={() => {
              navigate("/scoreboard");
            }}
            type="button"
          >
            Scoreboard
          </button>
          <button
            aria-current={desk.view === "instructions" ? "page" : undefined}
            className={
              desk.view === "instructions"
                ? "nav-button is-active"
                : "nav-button"
            }
            onClick={() => {
              void desk.openInstructions();
            }}
            type="button"
          >
            Agent instructions
          </button>
        </nav>
      </header>

      {desk.error !== "" && (
        <p className="error-banner" role="alert">
          {desk.error}
        </p>
      )}
      <DeskContent
        desk={desk}
        path={path}
        evaluationId={evaluationId}
        navigate={navigate}
        openClaim={openClaim}
      />
    </main>
  );
}
