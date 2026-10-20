import type { AgentRun } from "../api";

export function RunProgress({ run }: Readonly<{ run: AgentRun | null }>) {
  if (run === null) return null;
  const heading = run.status === "completed" ? "Run summary" : "Current run";

  return (
    <section className="card run-progress" aria-label="Agent run">
      <div className="run-heading">
        <div>
          <p className="eyebrow">{run.run_type.replaceAll("_", " ")} run</p>
          <h3>{heading}</h3>
          <p className="run-id">{run.run_id}</p>
        </div>
        <span
          className={`run-state run-state-${run.status.replaceAll(" ", "-")}`}
        >
          {run.status}
        </span>
      </div>
      {run.summary !== null && <p className="run-summary">{run.summary}</p>}
      {run.todos.length === 0 ? (
        <p className="muted-copy">No claims were selected for this run.</p>
      ) : (
        <ol className="run-todo-list">
          {run.todos.map((todo) => (
            <li key={todo.todo_id}>
              <span className="run-todo-copy">
                <strong>{todo.claim_id}</strong>
                <small>{todo.description}</small>
                {todo.attempt_count > 1 && (
                  <small>Attempts: {todo.attempt_count.toString()}</small>
                )}
              </span>
              <span
                className={`todo-state todo-state-${todo.status.replaceAll(" ", "-")}`}
              >
                {todo.status}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
