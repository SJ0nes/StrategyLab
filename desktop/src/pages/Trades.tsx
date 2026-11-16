import { useState } from "react";

import type { Check, DeploymentStatus, Strategy } from "../api";
import { whenLabel } from "../words";
import { decisionWords } from "./Deployed";

type Tab = "orders" | "days";

/** What an order filled at, or why there is no price to show. */
export function filledWords(check: Check): string {
  if (check.order_outcome !== "sent") return "";
  if (check.fill === null) return "Not yet";
  const { price, quantity, currency, filled_at } = check.fill;
  return `${quantity.toLocaleString("en-GB", { maximumFractionDigits: 6 })} at ${price.toLocaleString("en-GB", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })}${currency ? ` ${currency}` : ""}, ${whenLabel(filled_at)}`;
}

/** Every order the deployed strategy sent, and every day it decided, from its daily checks. */
export function Trades({ strategy, status }: { strategy: Strategy; status: DeploymentStatus | null }) {
  const [tab, setTab] = useState<Tab>("orders");
  const deployed = status?.deployment;
  const mine = deployed != null && deployed.available && deployed.strategy_id === strategy.id;
  const live = mine && status?.live != null && status.live.available ? status.live : null;

  if (!mine) {
    return (
      <>
        <div className="head">
          <div>
            <h1>Trades</h1>
          </div>
        </div>
        <div className="empty">
          <b>Nothing yet</b>
          {strategy.name} is not deployed. Trades appear here once it is.
        </div>
      </>
    );
  }
  const orders = (live?.checks ?? []).filter((check) => check.order_outcome === "sent" || check.order_outcome === "failed");

  return (
    <>
      <div className="head">
        <div>
          <h1>Trades</h1>
          <p>
            {tab === "orders"
              ? `Every order ${strategy.name} has sent to the practice account.`
              : `What ${strategy.name} decided at each daily check, including days it did nothing, and why.`}
          </p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {(
          [
            ["orders", "Orders"],
            ["days", "Daily decisions"],
          ] as [Tab, string][]
        ).map(([id, label]) => (
          <button type="button" role="tab" key={id} aria-selected={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "orders" ? (
        <div className="card">
          {orders.length === 0 ? (
            <p className="soft">No order yet.</p>
          ) : (
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Action</th>
                    <th>Venue</th>
                    <th>Filled</th>
                  </tr>
                </thead>
                <tbody>
                  {orders
                    .slice()
                    .reverse()
                    .map((check) => (
                      <tr key={check.recorded_at}>
                        <td>{check.bar_date ?? whenLabel(check.recorded_at)}</td>
                        <td>
                          <span className={`pill ${check.order_side === "buy" ? "good" : "warn"}`}>
                            {check.order_side === "buy" ? "Buy" : "Sell"}
                          </span>
                        </td>
                        <td>
                          <span className={`pill ${check.order_outcome === "sent" ? "good" : "bad"}`}>
                            {check.order_outcome === "sent" ? "Sent" : "Refused"}
                          </span>
                        </td>
                        <td className="soft">{filledWords(check)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
          {live !== null && !live.fills_readable && (
            <p className="soft">The fills log could not be read, so no fill price is shown.</p>
          )}
          <p className="soft" style={{ fontSize: "var(--t-s)" }}>
            "Sent" is the venue accepting a market order. The check runs after the close, so an order fills at a later
            session, and its price appears here after the next check. Fees are not taken off, and no result per trade is
            shown; the account value on the Dashboard is what the venue reported.
          </p>
        </div>
      ) : (
        <div className="card">
          {live === null || live.decisions.length === 0 ? (
            <p className="soft">No decision yet.</p>
          ) : (
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Decision</th>
                    <th>Rules met</th>
                    <th>Safety check</th>
                  </tr>
                </thead>
                <tbody>
                  {live.decisions.map((item) => (
                    <tr key={item.recorded_at}>
                      <td>{item.bar_date ?? whenLabel(item.recorded_at)}</td>
                      <td>{decisionWords(item.action, item.risk_verdict)}</td>
                      <td className="wrap soft">
                        {item.conditions_met.length} of {item.conditions_met.length + item.conditions_failed.length}
                      </td>
                      <td>
                        {item.risk_verdict === null ? (
                          ""
                        ) : (
                          <span className={`pill ${item.risk_verdict === "allow" ? "good" : "bad"}`} title={item.risk_reason ?? ""}>
                            {item.risk_verdict === "allow" ? "Allowed" : `Blocked: ${item.risk_reason ?? item.risk_verdict}`}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </>
  );
}
