/** The words the window explains on hover, as the approved prototype words them. */
const DEFINITIONS = {
  sharpe: ["Sharpe", "Return earned for the risk taken, per year. Above 1 is good, below 0 is losing."],
  fall: ["Worst fall", "Largest drop from a high point to a later low. Also called max drawdown."],
  luck: [
    "Luck test",
    "Runs the same search on scrambled price histories. The strategy must beat 95 in 100 of them to count as more than luck.",
  ],
  exam: ["Final exam", "One test on years the strategy never saw. Each look is counted and permanent."],
} as const;

/** A word with its meaning shown on hover or focus. */
export function Term({ word }: { word: keyof typeof DEFINITIONS }) {
  const [label, meaning] = DEFINITIONS[word];
  return (
    <span className="term" tabIndex={0} data-def={meaning}>
      {label}
    </span>
  );
}

/** The state of a page that is waiting for the server, or was refused by it. */
export function Waiting({ title, message }: { title: string; message: string | null }) {
  return (
    <>
      <div className="head">
        <div>
          <h1>{title}</h1>
        </div>
      </div>
      {message === null ? (
        <p className="soft">Reading…</p>
      ) : (
        <div className="empty" role="alert">
          <b>This could not be read</b>
          {message}
        </div>
      )}
    </>
  );
}
