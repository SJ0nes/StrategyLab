import { useState } from "react";

import {
  ApiError,
  type Deployed as DeployedRecord,
  type DeploymentStatus,
  type JobKind,
  type Live,
  type Strategy,
  changeDeployment,
} from "../api";
import { Chart } from "../components/Chart";
import { Term } from "../components/Term";
import { signedPct, versionLabel, whenLabel } from "../words";

/** A decision in a few words, from the action the strategy took and the risk layer's answer. */
export function decisionWords(action: string | null, verdict: string | null): string {
  const what = action === "enter_long" ? "Buy" : action === "exit_long" ? "Sell" : action === "hold" ? "No trade" : "Unknown";
  if (what === "No trade" || verdict === null || verdict === "allow") {
    return what;
  }
  return `${what}, blocked`;
}

/** A live figure, or the reason there is none. */
function figure(value: number | null, render: (value: number) => string, why: string | undefined): [string, string] {
  return value === null ? ["none", why ?? "not enough checks yet"] : [render(value), ""];
}

interface Props {
  strategy: Strategy;
  deployed: DeployedRecord;
  status: DeploymentStatus;
  onStart: (kind: JobKind) => void;
  onChanged: () => void;
}

/** The Dashboard of the deployed strategy: how it is doing on the practice account, and what it is doing now. */
export function Deployed({ strategy, deployed, status, onStart, onChanged }: Props) {
  const [problem, setProblem] = useState<string | null>(null);
  const live: Live | null = status.live !== null && status.live.available ? status.live : null;
  const checks = live?.checks ?? [];
  const last = checks[checks.length - 1];
  const decision = live?.decisions[0];
  const figures = live?.figures;
  const currency = last?.currency ?? "";
  const stopped = status.stop_all.stopped;
  const canCheck = !deployed.paused && !stopped && deployed.current && status.venue_unready === null;

  function change(action: "pause" | "resume" | "undeploy") {
    setProblem(null);
    changeDeployment(action)
      .then(() => onChanged())
      .catch((error: unknown) => setProblem(error instanceof ApiError ? error.message : "The change could not be made."));
  }

  const [ret, retWhy] = figure(figures?.return_pct ?? null, signedPct, figures?.why_missing.return_pct);
  const [sharpeText, sharpeWhy] = figure(figures?.sharpe ?? null, (value) => value.toFixed(2), figures?.why_missing.sharpe);
  const [fall, fallWhy] = figure(
    figures?.max_drawdown_pct ?? null,
    (value) => `−${value.toFixed(1)}%`,
    figures?.why_missing.max_drawdown_pct,
  );
  const expected = strategy.latest_search?.sharpe;

  return (
    <>
      <div className="head">
        <div>
          <h1>
            {strategy.name}{" "}
            <span className={`pill ${deployed.proven ? "good" : "warn"}`} style={{ verticalAlign: "middle" }}>
              {deployed.proven ? "Deployed" : "Deployed, unproven"}
            </span>
          </h1>
          <p>
            Deployed {whenLabel(deployed.deployed_at)} on the practice account. Settings: {versionLabel(deployed.params)}.
            {deployed.proven ? "" : " It did not pass the luck test: what it earns here tests the plumbing, not the strategy."}
          </p>
        </div>
        <span className="between">
          {deployed.paused ? (
            <button type="button" className="btn go" onClick={() => change("resume")}>
              Resume strategy
            </button>
          ) : (
            <button type="button" className="btn" onClick={() => change("pause")}>
              Pause strategy
            </button>
          )}
          <button type="button" className="btn" onClick={() => change("undeploy")}>
            Undeploy
          </button>
        </span>
      </div>
      {problem !== null && (
        <p className="down" role="alert">
          {problem}
        </p>
      )}
      {!deployed.current && (
        <div className="empty" role="alert">
          <b>The daily check is stopped</b>
          {deployed.problem}. Undeploy, then deploy it again to trade the file as it is now.
        </div>
      )}
      {status.live !== null && !status.live.available && (
        <div className="empty" role="alert">
          <b>The live figures could not be read</b>
          {status.live.reason}
        </div>
      )}

      <div className="kpis" data-tour="kpis">
        <div className="card kpi">
          <span className="label">Return since deployed</span>
          <span className={`big ${figures?.return_pct == null ? "" : figures.return_pct < 0 ? "down" : "up"}`}>{ret}</span>
          <small>{retWhy || `${checks.length} checks`}</small>
        </div>
        <div className="card kpi">
          <span className="label">
            <Term word="sharpe" /> (live)
          </span>
          <span className="big">{sharpeText}</span>
          <small>{sharpeWhy || (expected == null ? "" : `backtest ${expected.toFixed(2)}`)}</small>
        </div>
        <div className="card kpi">
          <span className="label">
            <Term word="fall" />
          </span>
          <span className="big">{fall}</span>
          <small>{fallWhy || "on the practice account"}</small>
        </div>
        <div className="card kpi">
          <span className="label">Orders sent</span>
          <span className="big">{figures?.sent ?? 0}</span>
          <small>{figures !== undefined && figures.failed > 0 ? `${figures.failed} refused by the venue` : "fill prices are not recorded"}</small>
        </div>
      </div>

      <div className="grid2">
        <div className="card" data-tour="chart">
          <h2>Account value{currency === "" ? "" : `, ${currency}`}</h2>
          {checks.length < 2 ? (
            <p className="soft">The line starts after two daily checks. {checks.length === 1 ? "One so far." : "None yet."}</p>
          ) : (
            <Chart
              dates={checks.map((check) => check.bar_date ?? check.recorded_at.slice(0, 10))}
              values={checks.map((check) => check.equity)}
              height={270}
              name="Account value"
            />
          )}
        </div>
        <div className="card" data-tour="now">
          <div className="between">
            <h2>Right now</h2>
            <span className={`pill ${deployed.paused || stopped ? "warn" : "good"}`}>
              {deployed.paused ? "Paused" : stopped ? "Stopped" : decision === undefined ? "No check yet" : decisionWords(decision.action, decision.risk_verdict)}
            </span>
          </div>
          {decision !== undefined && (
            <p className="soft">
              {decision.bar_date === null ? "" : `${decision.bar_date}: `}
              {decision.conditions_met.length > 0 ? `met ${decision.conditions_met.join("; ")}. ` : ""}
              {decision.risk_reason ?? ""}
            </p>
          )}
          <div className="rows">
            <div className="rowi">
              <span>Position</span>
              <b>{last?.position_quantity == null || last.position_quantity === 0 ? "No position" : `${last.position_quantity.toFixed(4)} units`}</b>
            </div>
            <div className="rowi">
              <span>Next automatic check</span>
              <b>{status.schedule.next_check === null ? (status.schedule.why_not ?? "none") : whenLabel(status.schedule.next_check)}</b>
            </div>
            <div className="rowi">
              <span>Loss lock</span>
              <span className={`pill ${live?.loss_lock ? "bad" : "good"}`}>
                {live?.loss_lock === true ? "Set: no buying today" : live?.loss_lock === false ? "Clear" : "No check today"}
              </span>
            </div>
            <div className="rowi">
              <span>Stop all</span>
              <span className={`pill ${stopped ? "bad" : "good"}`}>{stopped ? "On: no checks run" : "Off"}</span>
            </div>
            {status.schedule.missed.length > 0 && (
              <div className="rowi">
                <span>Checks missed</span>
                <span className="pill warn">{status.schedule.missed.length} weekday{status.schedule.missed.length === 1 ? "" : "s"}</span>
              </div>
            )}
          </div>
          <button type="button" className="btn small" disabled={!canCheck} onClick={() => onStart("daily_check")}>
            Check now
          </button>
          <p className="soft" style={{ fontSize: "var(--t-s)" }}>
            Checks run once a weekday after 17:30 UTC, only while this app is open.
            {status.venue_unready === null ? "" : ` ${status.venue_unready[0]?.toUpperCase()}${status.venue_unready.slice(1)}.`}
          </p>
        </div>
      </div>

      <div className="card" data-tour="trades">
        <div className="between">
          <h2>Recent decisions</h2>
          <a className="more" href="#trades">
            All trades →
          </a>
        </div>
        {live === null || live.decisions.length === 0 ? (
          <p className="soft">No decision yet. The first is made at the first daily check.</p>
        ) : (
          <div className="rows">
            {live.decisions.slice(0, 4).map((item) => (
              <div className="rowi" key={item.recorded_at}>
                <span>
                  {decisionWords(item.action, item.risk_verdict)}
                  <small>{item.bar_date ?? whenLabel(item.recorded_at)}</small>
                </span>
                <span className="soft">{item.risk_reason ?? ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
