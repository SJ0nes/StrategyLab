import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { SectionId } from "./Nav";

/** One stop: the section to show, the element to light, and what to say about it. */
export interface Stop {
  go: SectionId;
  target: string;
  title: string;
  text: string;
}

/**
 * The tour, stop by stop. Worded for what the app does today: every strategy
 * is a draft, prices are fetched by command, nothing is deployed. A stop
 * whose element is not on screen lights the middle of the window instead.
 */
export const STOPS: Stop[] = [
  {
    go: "dashboard",
    target: "pick",
    title: "Pick a strategy",
    text: "Everything on screen is about the strategy chosen here. Every one is a draft until it passes the luck test, and one read from a video appears here too.",
  },
  {
    go: "dashboard",
    target: "prices",
    title: "Price data",
    text: "The last day of prices stored. For now they are downloaded with strategylab fetch at the command line; the app does not update them yet.",
  },
  {
    go: "dashboard",
    target: "rules",
    title: "Its rules, in plain words",
    text: "Worded from the strategy file itself. For a strategy from a video, anything that could not be turned into a rule is listed in amber.",
  },
  {
    go: "dashboard",
    target: "path",
    title: "The path to deployment",
    text: "Confirm the rules, run a backtest, then the luck test. The button is on the step you have reached. A strategy that fails the luck test may still run on the practice account, marked unproven.",
  },
  {
    go: "dashboard",
    target: "live",
    title: "Live sections",
    text: "Dashboard, Trades and Tests: what a strategy has done. Trades fills once one is deployed.",
  },
  {
    go: "improve",
    target: "lab",
    title: "Workshop sections",
    text: "Backtest and Improve show what searches found, version by version. A search started here keeps running if you close the app.",
  },
  {
    go: "dashboard",
    target: "help",
    title: "Replay any time",
    text: "This button plays the tour again.",
  },
];

/** Where the spotlight and the tip go, in window pixels. */
export interface Placement {
  spot: { left: number; top: number; width: number; height: number };
  tip: { left: number; top: number };
}

const PAD = 8;
const GAP = 22;
const EDGE = 16;

/**
 * Place the spotlight around an element and the tip beside it: below if it
 * fits, else right, else left, else above, else the bottom right corner; and
 * never off the window.
 */
export function place(
  box: { left: number; top: number; width: number; height: number } | null,
  tip: { width: number; height: number },
  window: { width: number; height: number },
): Placement {
  const r = box ?? { left: window.width / 2, top: window.height / 2, width: 0, height: 0 };
  const right = r.left + r.width;
  const bottom = r.top + r.height;
  let x: number;
  let y: number;
  if (bottom + GAP + tip.height < window.height) {
    [x, y] = [r.left, bottom + GAP];
  } else if (right + GAP + tip.width < window.width) {
    [x, y] = [right + GAP, r.top];
  } else if (r.left - GAP - tip.width > 0) {
    [x, y] = [r.left - GAP - tip.width, r.top];
  } else if (r.top - GAP - tip.height > 0) {
    [x, y] = [r.left, r.top - GAP - tip.height];
  } else {
    [x, y] = [window.width - tip.width - EDGE, window.height - tip.height - EDGE];
  }
  return {
    spot: { left: r.left - PAD, top: r.top - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 },
    tip: {
      left: Math.max(EDGE, Math.min(x, window.width - tip.width - EDGE)),
      top: Math.max(EDGE, Math.min(y, window.height - tip.height - EDGE)),
    },
  };
}

/** The welcome dialog, with the prototype's line drawing itself across the top. */
export function Welcome({ onStart, onSkip }: { onStart: () => void; onSkip: () => void }) {
  return (
    <div className="modal open" role="dialog" aria-modal="true" aria-labelledby="welcome-title">
      <div className="dialog">
        <svg viewBox="0,0,480,150" role="img" aria-label="An account value line rising">
          <path
            className="draw b"
            pathLength={1}
            d="M10 126 C 60 130, 100 96, 150 104 S 230 60, 290 72 S 400 26, 470 18"
            fill="none"
            stroke="var(--accent)"
            strokeWidth="4"
          />
          <text
            className="fadein"
            x="466"
            y="46"
            textAnchor="end"
            style={{ fontSize: "15px", fontWeight: 700, fill: "var(--accent-ink)" }}
          >
            your strategy
          </text>
        </svg>
        <h2 id="welcome-title" style={{ fontSize: "var(--t-xl)", lineHeight: 1.15 }}>
          Welcome to StrategyLab
        </h2>
        <p>See where each strategy stands, every test run on it, and work on better versions.</p>
        <p className="soft">The tour takes under a minute.</p>
        <div className="between" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onSkip}>
            Skip
          </button>
          <button type="button" className="btn go" onClick={onStart}>
            Show me around
          </button>
        </div>
      </div>
    </div>
  );
}

/** A short burst of confetti at the end of the tour, unless the person has asked for less motion. */
export function confetti(canvas: HTMLCanvasElement | null): void {
  if (canvas === null || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    return;
  }
  const context = canvas.getContext("2d");
  if (context === null) {
    return;
  }
  const width = (canvas.width = window.innerWidth);
  const height = (canvas.height = window.innerHeight);
  const colours = ["#ff5f1f", "#35b779", "#fde725", "#31688e", "#e7e9ee"];
  const pieces = Array.from({ length: 140 }, (_, index) => ({
    x: width / 2,
    y: height * 0.38,
    vx: (Math.random() - 0.5) * 22,
    vy: -Math.random() * 17 - 4,
    size: 5 + Math.random() * 7,
    colour: colours[index % colours.length] ?? "#ff5f1f",
    angle: Math.random() * 6,
    spin: (Math.random() - 0.5) * 0.5,
  }));
  const start = performance.now();
  canvas.hidden = false;
  const frame = (now: number) => {
    const elapsed = now - start;
    context.clearRect(0, 0, width, height);
    for (const piece of pieces) {
      piece.vy += 0.45;
      piece.x += piece.vx;
      piece.y += piece.vy;
      piece.vx *= 0.985;
      piece.angle += piece.spin;
      context.save();
      context.translate(piece.x, piece.y);
      context.rotate(piece.angle);
      context.globalAlpha = Math.max(0, 1 - elapsed / 2200);
      context.fillStyle = piece.colour;
      context.fillRect(-piece.size / 2, -piece.size / 3, piece.size, piece.size / 1.6);
      context.restore();
    }
    if (elapsed < 2200) {
      requestAnimationFrame(frame);
    } else {
      context.clearRect(0, 0, width, height);
      canvas.hidden = true;
    }
  };
  requestAnimationFrame(frame);
}

interface Props {
  /** Show a section; the tour moves the window to each stop's section. */
  onGo: (section: SectionId) => void;
  /** The tour ended: true when it was finished, false when it was skipped. */
  onEnd: (finished: boolean) => void;
}

/** The spotlight tour. Rendered only while it runs. */
export function Tour({ onGo, onEnd }: Props) {
  const [index, setIndex] = useState(0);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const stop = STOPS[index];

  const measure = useCallback(() => {
    if (stop === undefined || tipRef.current === null) {
      return;
    }
    const element = document.querySelector(`[data-tour="${stop.target}"]`);
    element?.scrollIntoView?.({ block: "nearest" });
    const box = element?.getBoundingClientRect() ?? null;
    setPlacement(
      place(
        box,
        { width: tipRef.current.offsetWidth, height: tipRef.current.offsetHeight },
        { width: window.innerWidth, height: window.innerHeight },
      ),
    );
  }, [stop]);

  useEffect(() => {
    if (stop !== undefined) {
      onGo(stop.go);
    }
  }, [stop, onGo]);

  useLayoutEffect(() => {
    // Twice: once for the section change to render, once for its layout.
    const first = requestAnimationFrame(() => requestAnimationFrame(measure));
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(first);
      window.removeEventListener("resize", measure);
    };
  }, [measure]);

  if (stop === undefined) {
    return null;
  }
  const last = index === STOPS.length - 1;

  return (
    <div className="tour-on">
      <div id="tourblock" />
      <div id="spot" style={placement === null ? { display: "none" } : placement.spot} />
      <div
        id="tip"
        ref={tipRef}
        role="dialog"
        aria-live="polite"
        aria-label="Tour"
        // Unseen, not hidden, until it is measured: it must have a size to be placed.
        style={placement === null ? { opacity: 0 } : placement.tip}
      >
        <div className="txt" key={index}>
          <span className="eyebrow">
            {index + 1} of {STOPS.length}
          </span>
          <h2>{stop.title}</h2>
          <p>{stop.text}</p>
        </div>
        <div className="between">
          <div className="dots" aria-hidden="true">
            {STOPS.map((item, position) => (
              <i key={item.title} className={position === index ? "on" : ""} />
            ))}
          </div>
          <span>
            <button type="button" className="btn small" onClick={() => onEnd(false)}>
              Skip
            </button>{" "}
            {index > 0 && (
              <>
                <button type="button" className="btn small" onClick={() => setIndex((current) => Math.max(0, current - 1))}>
                  Back
                </button>{" "}
              </>
            )}
            <button
              type="button"
              className="btn small go"
              autoFocus
              onClick={() => (last ? onEnd(true) : setIndex((current) => Math.min(STOPS.length - 1, current + 1)))}
            >
              {last ? "Finish" : "Next"}
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
