type InstructionsPageProps = Readonly<{ instructions: string | null }>;

export function InstructionsPage({ instructions }: InstructionsPageProps) {
  return (
    <section className="instructions-page card">
      <p className="eyebrow">Configuration</p>
      <h2>UnderCover Agent instructions</h2>
      <p className="muted-copy">These instructions are read-only.</p>
      {instructions === null ? (
        <p>Loading instructions…</p>
      ) : (
        <pre className="instruction-copy">{instructions}</pre>
      )}
    </section>
  );
}
