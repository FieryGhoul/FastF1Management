import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import {
  Moon,
  Sun,
  Radio,
  ArrowUpRight,
  Database,
  AlertTriangle,
  LoaderCircle,
  MapPinned,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import type { RaceEvent, TrackPoint } from "./types";
import { formatValue, localDate } from "./api";

export function Layout({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState(
    () =>
      localStorage.getItem("theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  const nav = [
    ["/", "Overview"],
    ["/live", "Live"],
    ["/calendar", "Calendar"],
    ["/standings", "Standings"],
    ["/drivers", "Drivers"],
    ["/teams", "Teams"],
    ["/circuits", "Circuits"],
  ];
  return (
    <div className="app-shell">
      <header className="site-header">
        <Link to="/" className="wordmark" aria-label="Race Data home">
          <span>RD</span>
          <b>RACE DATA</b>
        </Link>
        <nav className="primary-nav" aria-label="Primary navigation">
          {nav.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              {label === "Live" && <i className="live-dot" />}
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="header-actions">
          <NavLink className="admin-link" to="/admin">
            <Database size={15} /> Ops
          </NavLink>
          <button
            className="icon-button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label="Toggle colour theme"
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </button>
        </div>
      </header>
      <main>{children}</main>
      <footer>
        <span>RACE DATA / FASTF1</span>
        <span>Timing data is informational and may be delayed.</span>
      </footer>
      <UpdateBridge />
    </div>
  );
}

export function UpdateBridge() {
  const client = useQueryClient();
  useEffect(() => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(
      `${protocol}//${location.host}/api/v1/updates`,
    );
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.event === "sync.completed") client.invalidateQueries();
    };
    return () => socket.close();
  }, [client]);
  return null;
}

export function PageHeader({
  eyebrow,
  title,
  copy,
  aside,
}: {
  eyebrow: string;
  title: string;
  copy?: string;
  aside?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {copy && <p>{copy}</p>}
      </div>
      {aside && <div>{aside}</div>}
    </header>
  );
}

export function Status({
  kind = "neutral",
  children,
}: {
  kind?: "live" | "good" | "warn" | "neutral";
  children: ReactNode;
}) {
  return (
    <span className={`status status-${kind}`}>
      {kind === "live" && <Radio size={13} />} {children}
    </span>
  );
}

export function Empty({
  title,
  copy,
  loading = false,
}: {
  title: string;
  copy: string;
  loading?: boolean;
}) {
  return (
    <div className="empty-state">
      {loading ? <LoaderCircle className="spin" /> : <AlertTriangle />}
      <h3>{title}</h3>
      <p>{copy}</p>
    </div>
  );
}

export function ErrorState({ error }: { error: Error }) {
  return <Empty title="Data unavailable" copy={error.message} />;
}

export function EventCard({
  event,
  compact = false,
}: {
  event: RaceEvent;
  compact?: boolean;
}) {
  const race =
    event.sessions.find((s) => s.code === "R") ?? event.sessions.at(-1);
  return (
    <Link
      to={`/events/${event.season}/${event.round}`}
      className={`event-card ${compact ? "compact" : ""}`}
    >
      <div className="event-round">R{String(event.round).padStart(2, "0")}</div>
      <div className="event-body">
        <span>{event.country}</span>
        <h3>{event.name.replace(" Grand Prix", "")}</h3>
        <p>
          {event.location} · {localDate(race?.starts_at)}
        </p>
      </div>
      <ArrowUpRight className="event-arrow" />
    </Link>
  );
}

export function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: ReactNode;
  detail?: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: string[];
  active: string;
  onChange: (tab: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          role="tab"
          aria-selected={active === tab}
          key={tab}
          onClick={() => onChange(tab)}
        >
          {tab}
        </button>
      ))}
    </div>
  );
}

export function TrackMap({
  points = [],
  corners = [],
  rotation = 0,
  label,
  emptyMessage,
}: {
  points?: TrackPoint[];
  corners?: TrackPoint[];
  rotation?: number;
  label?: string;
  emptyMessage?: string;
}) {
  const geometry = useMemo(() => {
    const validPoints = points.filter(
      (point) => Number.isFinite(point.X) && Number.isFinite(point.Y),
    );
    if (validPoints.length < 2) return null;
    const radians = (rotation * Math.PI) / 180;
    const rotate = (point: TrackPoint) => ({
      ...point,
      X: point.X * Math.cos(radians) + point.Y * Math.sin(radians),
      Y: -point.X * Math.sin(radians) + point.Y * Math.cos(radians),
    });
    const trackPoints = validPoints.map(rotate);
    const cornerPoints = corners
      .filter((point) => Number.isFinite(point.X) && Number.isFinite(point.Y))
      .map(rotate);
    const xs = trackPoints.map((p) => p.X),
      ys = trackPoints.map((p) => p.Y);
    const minX = Math.min(...xs),
      maxX = Math.max(...xs),
      minY = Math.min(...ys),
      maxY = Math.max(...ys);
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    const factor = Math.min(730 / spanX, 390 / spanY);
    const offsetX = (800 - spanX * factor) / 2;
    const offsetY = (460 - spanY * factor) / 2;
    const scale = (p: TrackPoint) => ({
      x: offsetX + (p.X - minX) * factor,
      y: offsetY + (maxY - p.Y) * factor,
    });
    return {
      line: trackPoints
        .map((p, i) => {
          const q = scale(p);
          return `${i ? "L" : "M"}${q.x.toFixed(1)},${q.y.toFixed(1)}`;
        })
        .join(" "),
      corners: cornerPoints.map((p) => ({ ...p, ...scale(p) })),
      start: scale(trackPoints[0]),
    };
  }, [points, corners, rotation]);
  if (!geometry)
    return (
      <div className="track-placeholder">
        <span>{label ?? "TRACK MAP"}</span>
        <div className="track-scan" aria-hidden>
          <MapPinned />
        </div>
        <small>
          {emptyMessage ??
            "Generating the real outline from FastF1 position data."}
        </small>
      </div>
    );
  return (
    <svg
      className="track-map"
      viewBox="0 0 800 460"
      role="img"
      aria-label={`${label ?? "Circuit"} track outline`}
    >
      <path d={geometry.line} />
      <rect
        className="start-finish"
        x={geometry.start.x - 5}
        y={geometry.start.y - 5}
        width="10"
        height="10"
      />
      {geometry.corners.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r="11" />
          <text x={p.x} y={p.y + 4}>
            {p.Number}
            {p.Letter}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function YearSelect({
  year,
  setYear,
  modernOnly = false,
}: {
  year: number;
  setYear: (year: number) => void;
  modernOnly?: boolean;
}) {
  const current = new Date().getFullYear(),
    first = modernOnly ? 2018 : 1950;
  return (
    <label className="select-label">
      Season
      <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
        {Array.from({ length: current - first + 1 }, (_, i) => current - i).map(
          (y) => (
            <option key={y}>{y}</option>
          ),
        )}
      </select>
    </label>
  );
}

export function DataTable({
  columns,
  rows,
}: {
  columns: {
    key: string;
    label: string;
    render?: (row: Record<string, unknown>) => ReactNode;
  }[];
  rows: Record<string, unknown>[];
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={String(row.id ?? row.driverId ?? row.constructorId ?? i)}>
              {columns.map((c) => (
                <td key={c.key}>
                  {c.render ? c.render(row) : formatValue(row[c.key], c.key)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
