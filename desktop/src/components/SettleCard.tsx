import { useState } from "react";

import { ApiError, type Settlement, type Strategy, settleDifference } from "../api";
import { whenLabel } from "../words";

/** A number of units, signed, without trailing zeros: "−2", "+0.5". */
export function signedUnits(units: number): string {
  const plain = Number(Math.abs(units).toFixed(6)).toString();
  return `${units < 0 ? "−" : "+"}${plain}`;
}

/** "unit" for one, "units" for any other number. */
export function unitWord(units: number): string {
  return Math.abs(units) === 1 ? "unit" : "units";
}

interface Props {
  strategy: Strategy;
  settlement: Settlement;
  onChanged: () => void;
}

/**
 * Says when the strategies' units and Trading 212's position disagree, and lets
 * the operator give the whole difference to this strategy once its name is typed
 * (`docs/decisions/0024`). The server works the difference out again, and refuses
 * one that is not the figure shown, so this card decides nothing. Nothing shows
 * while they agree.
 */
export function SettleCard({ strategy, settlement, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (settlement.state === "agree") {
    return null;
  }
  if (settlement.state === "unreadable") {
    return (
      <div className="empty" role="alert">
        <b>Whether the records and Trading 212 agree could not be read</b>
        {settlement.why}
      </div>
    );
  }
  const { difference } = settlement;

  function go() {
    setBusy(true);
    setProblem(null);
    settleDifference(strategy.id, typed, difference)
      .then(() => {
        setOpen(false);
        onChanged();
      })
      .catch((error: unknown) =>
        setProblem(error instanceof ApiError ? error.message : "The correction could not be recorded."),
      )
      .finally(() => setBusy(false));
  }

  // The dialog sits beside the card, not in it: the card's styles for <b> would reach into it.
  return (
    <>
      <div className="empty" role="alert">
        <b>The records and Trading 212 disagree, so no check sends an order</b>
      At the check {whenLabel(settlement.snapshot_at)}, Trading 212 held {settlement.venue_units} units of{" "}
      {settlement.ticker}; the strategies' recorded trades add up to {settlement.recorded_units}. The difference is{" "}
      {signedUnits(difference)} {unitWord(difference)}.{" "}
      {settlement.state === "wait" ? (
        <>It cannot be settled yet: {settlement.why}.</>
      ) : (
        <>
          If it belongs to {strategy.name}, sold or bought by hand in the Trading 212 app, give it to{" "}
          {strategy.name}. No order is sent.{" "}
          <button type="button" className="btn" onClick={() => setOpen(true)}>
            Give the difference to {strategy.name}…
          </button>
        </>
      )}
      </div>
      {open && (
        <div className="modal open" role="dialog" aria-modal="true" aria-labelledby="settle-title">
          <div className="dialog">
            <h2 id="settle-title" style={{ fontSize: "var(--t-l)" }}>
              Give {signedUnits(difference)} {unitWord(difference)} to {strategy.name}
            </h2>
            <p>
              A correction is added to the records, and {strategy.name}'s units change by {signedUnits(difference)}.
              Nothing earlier is erased, and no order is sent. Its own return leaves out a trade the correction opens,
              adds to or closes.
            </p>
            <p>
              Type <b className="mono">{strategy.name}</b> to confirm.
            </p>
            <input
              type="text"
              value={typed}
              autoComplete="off"
              aria-label="Strategy name"
              onChange={(event) => setTyped(event.target.value)}
            />
            {problem !== null && (
              <p className="down" role="alert">
                {problem}
              </p>
            )}
            <div className="between" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btn go" disabled={busy || typed.trim() !== strategy.name} onClick={go}>
                Give it to {strategy.name}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
