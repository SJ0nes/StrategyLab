import { useState } from "react";

import { dayLabel, money } from "../words";

/** The drawing's own coordinate space; the SVG scales to the width it is given. */
const WIDTH = 900;
const LEFT = 58;
const RIGHT = 12;
const TOP = 8;
const BOTTOM = 24;

/** The points of a curve that have a value, with the date each was recorded on. */
export function drawable(dates: string[], values: (number | null)[]): { date: string; value: number }[] {
  const points: { date: string; value: number }[] = [];
  values.forEach((value, index) => {
    const date = dates[index];
    if (value !== null && date !== undefined) {
      points.push({ date, value });
    }
  });
  return points;
}

/** Round gridline spacing that gives about four lines across `span`. */
export function gridStep(span: number): number {
  const raw = span / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((multiple) => multiple * magnitude).find((step) => step >= raw) ?? raw;
}

interface Props {
  dates: string[];
  /** Account value at each date; null where there is none (before warm-up ends). */
  values: (number | null)[];
  /** Height in the drawing's units; the width is always 900. */
  height: number;
  /** What the line is, for the legend, the tooltip and screen readers. */
  name: string;
}

/** One line of account value over time, with a tooltip that follows the pointer. */
export function Chart({ dates, values, height, name }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const points = drawable(dates, values);
  const first = points[0];
  const last = points[points.length - 1];
  if (first === undefined || last === undefined || points.length < 2) {
    return <p className="soft">No growth line was recorded for this version.</p>;
  }

  const numbers = points.map((point) => point.value);
  const pad = (Math.max(...numbers) - Math.min(...numbers)) * 0.1 || 1;
  const low = Math.min(...numbers) - pad;
  const high = Math.max(...numbers) + pad;
  const plotWidth = WIDTH - LEFT - RIGHT;
  const plotHeight = height - TOP - BOTTOM;
  const x = (index: number) => LEFT + (index / (points.length - 1)) * plotWidth;
  const y = (value: number) => TOP + plotHeight - ((value - low) / (high - low)) * plotHeight;
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)} ${y(point.value).toFixed(1)}`)
    .join(" ");

  const step = gridStep(high - low);
  const lines: number[] = [];
  for (let value = Math.ceil(low / step) * step; value <= high; value += step) {
    lines.push(value);
  }

  const hovered = hover === null ? undefined : points[hover];

  function follow(event: React.MouseEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width === 0) {
      return;
    }
    const fraction = ((event.clientX - box.left) * (WIDTH / box.width) - LEFT) / plotWidth;
    setHover(Math.round(Math.max(0, Math.min(1, fraction)) * (points.length - 1)));
  }

  return (
    <div className="chartbox">
      <div className="legend">
        <span>
          <i />
          {name}
          <b>{money(last.value)}</b>
        </span>
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`${name}, ${dayLabel(first.date)} to ${dayLabel(last.date)}`}
        onMouseMove={follow}
        onMouseLeave={() => setHover(null)}
      >
        {lines.map((value) => (
          <g key={value}>
            <line x1={LEFT} x2={WIDTH - RIGHT} y1={y(value)} y2={y(value)} stroke="var(--rule)" />
            <text x={LEFT - 8} y={y(value) + 4} textAnchor="end">
              {money(value)}
            </text>
          </g>
        ))}
        <line
          x1={LEFT}
          x2={WIDTH - RIGHT}
          y1={TOP + plotHeight}
          y2={TOP + plotHeight}
          stroke="var(--ink-soft)"
          opacity=".5"
        />
        <text x={LEFT} y={height - 5}>
          {dayLabel(first.date)}
        </text>
        <text x={WIDTH - RIGHT} y={height - 5} textAnchor="end">
          {dayLabel(last.date)}
        </text>
        <path
          d={path}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2.4"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle
          cx={x(points.length - 1)}
          cy={y(last.value)}
          r="5"
          fill="var(--accent)"
          stroke="var(--panel)"
          strokeWidth="2"
        />
        {hovered !== undefined && hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={TOP} y2={TOP + plotHeight} stroke="var(--ink-soft)" />
            <circle
              cx={x(hover)}
              cy={y(hovered.value)}
              r="5"
              fill="var(--accent)"
              stroke="var(--panel)"
              strokeWidth="2"
            />
          </g>
        )}
      </svg>
      {hovered !== undefined && hover !== null && (
        <div
          className="tipbox"
          style={{
            display: "block",
            top: "2rem",
            ...(hover > points.length / 2
              ? { right: `${100 - (x(hover) / WIDTH) * 100 + 2}%` }
              : { left: `${(x(hover) / WIDTH) * 100 + 2}%` }),
          }}
        >
          <div className="d">{dayLabel(hovered.date)}</div>
          <div>
            <i />
            {name}
            <b>{money(hovered.value)}</b>
          </div>
        </div>
      )}
    </div>
  );
}
