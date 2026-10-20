type InstructionsPageProps = Readonly<{
  instructions: {
    queue_instructions: string;
    claim_instructions: string;
  } | null;
}>;

export function InstructionsPage({ instructions }: InstructionsPageProps) {
  return (
    <section className="instructions-page card">
      <p className="eyebrow">Configuration</p>
      <h2>UnderCover Agent instructions</h2>
      <p className="muted-copy">These instructions are read-only.</p>
      {instructions === null ? (
        <p>Loading instructions…</p>
      ) : (
        <div className="instruction-grid">
          <section>
            <h3>Queue agent</h3>
            <pre className="instruction-copy">
              {instructions.queue_instructions}
            </pre>
          </section>
          <section>
            <h3>Claim agent</h3>
            <pre className="instruction-copy">
              {instructions.claim_instructions}
            </pre>
          </section>
        </div>
      )}
    </section>
  );
}
