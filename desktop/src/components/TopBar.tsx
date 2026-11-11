import type { Job, JobKind, Overview, Strategy } from "../api";

interface Props {
  overview: Overview;
  strategy: Strategy | undefined;
  onChoose: (id: string) => void;
  /** The job that is running, if any. */
  job: Job | null;
  onOpenJob: (kind: JobKind) => void;
}

/** What the running-job button says. */
export function jobLabel(job: Job): string {
  const name = job.kind === "luck_test" ? "Luck test" : "Search";
  const state = job.status === "stopping" ? "stopping" : "running";
  return job.progress === null ? `${name} ${state}` : `${name} ${state}: ${job.progress.finished} of ${job.progress.total}`;
}

/** What the account tag says, from what the server could see of the venue. */
export function accountLabel(venue: Overview["venue"]): string {
  if (!venue.key_present) {
    return "No venue key set";
  }
  return venue.environment === "demo" ? "Practice account" : "Venue not set to practice";
}

export function TopBar({ overview, strategy, onChoose, job, onOpenJob }: Props) {
  const { prices } = overview;
  return (
    <div className="bar">
      <div className="brand">
        STRATEGY<span>LAB</span>
      </div>
      <div className="pick">
        <label htmlFor="strat">Strategy</label>
        <select
          id="strat"
          value={strategy?.id ?? ""}
          onChange={(event) => onChoose(event.target.value)}
          disabled={overview.strategies.length === 0}
        >
          {/* Nothing can be deployed yet, so the only group is drafts. */}
          <optgroup label="Drafts, not deployed">
            {overview.strategies.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </optgroup>
        </select>
        {strategy !== undefined && (
          <span className="live off">
            <i />
            Draft
          </span>
        )}
      </div>
      <div className="right">
        {job !== null && (
          <button type="button" className="btn small" onClick={() => onOpenJob(job.kind)}>
            <span className="up">●</span> {jobLabel(job)}
          </button>
        )}
        <span className="pill" title={prices.available ? `Last bar ${prices.last_bar}` : prices.reason}>
          {prices.available ? (
            <>
              <span className="up">●</span> Prices stored to {prices.last_bar}
            </>
          ) : (
            <>
              <span className="down">●</span> No price data
            </>
          )}
        </span>
        <span className="pill">{accountLabel(overview.venue)}</span>
      </div>
    </div>
  );
}
