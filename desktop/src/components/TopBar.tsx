import type { DeploymentStatus, Job, JobKind, Overview, Strategy } from "../api";

interface Props {
  overview: Overview;
  strategy: Strategy | undefined;
  onChoose: (id: string) => void;
  /** The job that is running, if any. */
  job: Job | null;
  onOpenJob: (kind: JobKind) => void;
  onTour: () => void;
  deployment: DeploymentStatus | null;
  onStopAll: () => void;
}

/** What the running-job button says. */
export function jobLabel(job: Job): string {
  const name = job.kind === "luck_test" ? "Luck test" : job.kind === "daily_check" ? "Daily check" : job.kind === "final_exam" ? "Final exam" : "Search";
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

export function TopBar({ overview, strategy, onChoose, job, onOpenJob, onTour, deployment, onStopAll }: Props) {
  const deployed = deployment?.deployment != null && deployment.deployment.available ? deployment.deployment : null;
  const stopped = deployment?.stop_all.stopped ?? false;
  const { prices } = overview;
  return (
    <div className="bar">
      <div className="brand">
        STRATEGY<span>LAB</span>
      </div>
      <div className="pick" data-tour="pick">
        <label htmlFor="strat">Strategy</label>
        <select
          id="strat"
          value={strategy?.id ?? ""}
          onChange={(event) => onChoose(event.target.value)}
          disabled={overview.strategies.length === 0}
        >
          {deployed !== null && (
            <optgroup label="Deployed">
              {overview.strategies
                .filter((item) => item.id === deployed.strategy_id)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
            </optgroup>
          )}
          <optgroup label="Drafts, not deployed">
            {overview.strategies
              .filter((item) => item.id !== deployed?.strategy_id)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </optgroup>
        </select>
        {strategy !== undefined &&
          (deployed !== null && deployed.strategy_id === strategy.id ? (
            <span className={`live${deployed.paused || stopped ? " off" : ""}`}>
              <i />
              {deployed.paused ? "Paused" : stopped ? "Stopped" : "Running"}
            </span>
          ) : (
            <span className="live off">
              <i />
              Draft
            </span>
          ))}
      </div>
      <div className="right">
        {job !== null && (
          <button type="button" className="btn small" onClick={() => onOpenJob(job.kind)}>
            <span className="up">●</span> {jobLabel(job)}
          </button>
        )}
        <span className="pill" data-tour="prices" title={prices.available ? `Last bar ${prices.last_bar}` : prices.reason}>
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
        {overview.kill_switch === true && (
          <span className="pill bad" title="Set in configs/risk_limits.yaml; the risk layer refuses every order">
            Risk kill switch on
          </span>
        )}
        <button type="button" className="btn small" onClick={onStopAll} disabled={deployment === null}>
          {stopped ? (
            <>
              <span className="down">■</span> All trading stopped
            </>
          ) : (
            "Stop all trading"
          )}
        </button>
        <button type="button" className="btn small" data-tour="help" onClick={onTour}>
          ▶ Tour
        </button>
      </div>
    </div>
  );
}
