import type { ClaimDetail } from "../api";

export function KnowledgeReadsCard({
  detail,
}: Readonly<{ detail: ClaimDetail }>) {
  return (
    <section className="card content-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Decision sources</p>
          <h3>Policy and manual sections read</h3>
        </div>
      </div>
      {detail.source_reads.length === 0 ? (
        <p className="muted-copy">
          The agent has not read a source section yet.
        </p>
      ) : (
        <div className="source-read-list">
          {detail.source_reads.map((sourceRead) => (
            <details className="source-read-entry" key={sourceRead.section_id}>
              <summary>
                <span>
                  <strong>{sourceRead.heading}</strong>
                  <small>
                    {sourceRead.source.replaceAll("_", " ")} · v
                    {sourceRead.version}
                  </small>
                </span>
                <small>{sourceRead.section_id}</small>
              </summary>
              <pre>{sourceRead.content}</pre>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
