import { ClaimQueue } from "./components/ClaimQueue";
import { ClaimWorkspace } from "./components/ClaimWorkspace";
import { InstructionsPage } from "./components/InstructionsPage";
import "./App.css";
import { useClaimsDesk } from "./useClaimsDesk";

export function App() {
  const desk = useClaimsDesk();
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
            aria-current={desk.view === "queue" ? "page" : undefined}
            className={
              desk.view === "queue" ? "nav-button is-active" : "nav-button"
            }
            onClick={() => {
              desk.setView("queue");
            }}
            type="button"
          >
            Claims queue
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

      {desk.view === "instructions" ? (
        <InstructionsPage instructions={desk.instructions} />
      ) : (
        <div className="desk-layout">
          <ClaimQueue
            claims={desk.claims}
            selectedClaimId={desk.selectedClaimId}
            onSelect={desk.setSelectedClaimId}
          />
          <section className="detail-panel" aria-label="Claim details">
            {desk.detail === null ? (
              <div className="card loading-card">
                <p>
                  {desk.claims.length === 0
                    ? "Loading claims…"
                    : "Select a claim to review."}
                </p>
              </div>
            ) : (
              <ClaimWorkspace
                actionBusy={desk.actionBusy}
                detail={desk.detail}
                onHandlerChange={desk.setSelectedHandler}
                onPay={desk.handlePay}
                onRun={desk.handleRun}
                onSignOff={desk.handleSignOff}
                selectedHandler={desk.selectedHandler}
              />
            )}
          </section>
        </div>
      )}
    </main>
  );
}
