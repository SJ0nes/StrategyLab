import { useState } from "react";

import { ApiError, type Strategy, deployStrategy } from "../api";
import { versionLabel } from "../words";

interface Props {
  strategy: Strategy;
  onDone: () => void;
  onClose: () => void;
}

/**
 * Deploy a strategy to the practice account, once its name has been typed.
 *
 * The server checks everything again - the name, the confirmation, the search,
 * the account, that nothing else is deployed - so this dialog decides nothing.
 */
export function DeployDialog({ strategy, onDone, onClose }: Props) {
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const search = strategy.latest_search;
  const proven = search?.gate.status === "passed";

  function go() {
    setBusy(true);
    setProblem(null);
    deployStrategy(strategy.id, typed)
      .then(() => onDone())
      .catch((error: unknown) =>
        setProblem(error instanceof ApiError ? error.message : "The deployment could not be made."),
      )
      .finally(() => setBusy(false));
  }

  return (
    <div className="modal open" role="dialog" aria-modal="true" aria-labelledby="deploy-title">
      <div className="dialog">
        <h2 id="deploy-title" style={{ fontSize: "var(--t-l)" }}>
          Deploy {strategy.name} to the practice account
        </h2>
        <p>
          It trades {versionLabel(search?.candidate ?? null)} once a weekday after the close, while this app is
          open, with practice money. Pause, stop all or undeploy at any time.
        </p>
        {!proven && (
          <div className="rule" style={{ borderColor: "var(--warn-rule)", background: "var(--warn-bg)" }}>
            <b>Unproven.</b> It did not pass the luck test. It may still run on the practice account, but what it earns
            there tests the plumbing and the simulator, not the strategy.
          </div>
        )}
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
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn go" disabled={busy || typed.trim() !== strategy.name} onClick={go}>
            Deploy
          </button>
        </div>
      </div>
    </div>
  );
}
