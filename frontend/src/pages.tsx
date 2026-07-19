import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  CalendarDays,
  Check,
  Clock3,
  Database,
  Gauge,
  LockKeyhole,
  Map,
  Radio,
  Search,
  Wrench,
} from "lucide-react";
import { api, duration, localDate } from "./api";
import {
  DataTable,
  Empty,
  ErrorState,
  EventCard,
  Metric,
  PageHeader,
  Status,
  Tabs,
  TrackMap,
  YearSelect,
} from "./components";
import type { ApiEnvelope, Circuit, Job, LiveState, RaceEvent } from "./types";

const TelemetryChart = lazy(() => import("./TelemetryChart"));

const currentYear = new Date().getFullYear();

function useCalendar(year: number) {
  return useQuery({
    queryKey: ["calendar", year],
    queryFn: () => api<ApiEnvelope<RaceEvent[]>>(`/calendar/${year}`),
  });
}

export function HomePage() {
  const { data, isLoading, error } = useCalendar(currentYear);
  const live = useQuery({
    queryKey: ["live"],
    queryFn: () => api<LiveState>("/live"),
    refetchInterval: 60_000,
  });
  const events = data?.data ?? [],
    now = Date.now();
  const next =
    live.data?.event ??
    events.find((e) =>
      e.sessions.some(
        (s) => s.starts_at && new Date(s.starts_at).getTime() > now,
      ),
    );
  const completed = events.filter(
    (e) =>
      e.sessions.at(-1)?.starts_at &&
      new Date(e.sessions.at(-1)!.starts_at!).getTime() < now,
  );
  const circuits = useQuery({
    queryKey: ["circuits"],
    queryFn: () => api<ApiEnvelope<Circuit[]>>("/circuits"),
  });
  const nextCircuit = circuits.data?.data.find((circuit) => {
    const location = next?.location?.toLowerCase();
    return Boolean(
      location &&
        (circuit.locality?.toLowerCase() === location ||
          circuit.name.toLowerCase().includes(location)),
    );
  });
  const homeMap = useQuery({
    queryKey: ["circuit-map", nextCircuit?.slug],
    queryFn: () => api<any>(`/circuits/${nextCircuit!.slug}/map`),
    enabled: Boolean(nextCircuit && !nextCircuit.map_data),
    refetchInterval: (result) =>
      result.state.data?.availability === "awaiting_data" ? 1000 : false,
  });
  const homeMapData = nextCircuit?.map_data ?? homeMap.data?.data;
  const measuredDistance = (homeMapData?.points ?? []).reduce(
    (maximum: number, point: { Distance?: number }) =>
      Math.max(maximum, point.Distance ?? 0),
    0,
  );
  const lapLength =
    nextCircuit?.length_km ??
    (measuredDistance > 0 ? measuredDistance / 1000 : undefined);
  const nextSession =
    live.data?.session ??
    next?.sessions.find(
      (session) =>
        session.starts_at && new Date(session.starts_at).getTime() > now,
    );
  const raceSession = next?.sessions.find((session) => session.code === "R");
  const cornerCount = new Set(
    (homeMapData?.corners ?? [])
      .map((corner: { Number?: number }) => corner.Number)
      .filter((number: number | undefined) => number != null),
  ).size;
  if (error)
    return (
      <div className="page">
        <ErrorState error={error} />
      </div>
    );
  return (
    <div className="page home-page">
      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow">
            <span className="slash" /> Formula 1 intelligence
          </div>
          <h1>
            Every session.
            <br />
            <em>One clear view.</em>
          </h1>
          <p>
            Schedules, standings, circuit detail and lap-level telemetry—built
            directly on FastF1 data.
          </p>
          <div className="hero-actions">
            <Link className="button primary" to="/live">
              <Radio /> Open live centre
            </Link>
            <Link className="button ghost" to="/calendar">
              Season calendar <ArrowRight />
            </Link>
          </div>
        </div>
        <div className="hero-track">
          <TrackMap
            label={next?.location ?? "NEXT CIRCUIT"}
            points={homeMapData?.points}
            corners={homeMapData?.corners}
            rotation={homeMapData?.rotation}
          />
          {next && nextCircuit && (
            <div className="hero-track-details">
              <div className="hero-track-heading">
                <div>
                  <span>Next circuit</span>
                  <h2>{nextCircuit.name}</h2>
                  <p>
                    {nextCircuit.locality ?? next.location}, {next.country}
                  </p>
                </div>
                <Link to={`/circuits/${nextCircuit.slug}`}>
                  Circuit details <ArrowRight />
                </Link>
              </div>
              <div className="hero-track-facts">
                <div>
                  <span>Round</span>
                  <strong>{next.round}</strong>
                </div>
                <div>
                  <span>Lap length</span>
                  <strong>
                    {lapLength ? `${lapLength.toFixed(3)} km` : "TBC"}
                  </strong>
                </div>
                <div>
                  <span>Corners</span>
                  <strong>{cornerCount || "TBC"}</strong>
                </div>
              </div>
              <div className="hero-track-schedule">
                <div>
                  <span>Next on track</span>
                  <strong>{nextSession?.name ?? "Schedule pending"}</strong>
                  <small>{localDate(nextSession?.starts_at)}</small>
                </div>
                <div>
                  <span>Grand Prix</span>
                  <strong>{raceSession?.name ?? next.name}</strong>
                  <small>{localDate(raceSession?.starts_at)}</small>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>
      <section className="ticker">
        <span>
          {live.data?.state === "in_progress" ? "ON TRACK" : "NEXT UP"}
        </span>
        <b>{next?.name ?? "Season schedule loading"}</b>
        <small>
          {live.data?.session
            ? `${live.data.session.name} · ${localDate(live.data.session.starts_at)}`
            : "Official schedule via FastF1"}
        </small>
      </section>
      <section className="section">
        <div className="section-title">
          <div>
            <span>Season pulse</span>
            <h2>{currentYear} championship</h2>
          </div>
          <Link to="/standings">
            Full standings <ArrowRight />
          </Link>
        </div>
        <div className="metric-grid">
          <Metric
            label="Rounds"
            value={events.length || "—"}
            detail="Official calendar"
          />
          <Metric
            label="Completed"
            value={completed.length}
            detail={`${Math.round((completed.length / (events.length || 1)) * 100)}% of season`}
          />
          <Metric
            label="Next round"
            value={next ? `R${next.round}` : "—"}
            detail={next?.country}
          />
          <Metric
            label="Data status"
            value={isLoading ? "SYNC" : "READY"}
            detail={data?.source}
          />
        </div>
      </section>
      <section className="section">
        <div className="section-title">
          <div>
            <span>Calendar</span>
            <h2>Coming up</h2>
          </div>
          <Link to="/calendar">
            All rounds <ArrowRight />
          </Link>
        </div>
        <div className="event-grid">
          {events
            .filter((e) =>
              e.sessions.some(
                (s) => s.starts_at && new Date(s.starts_at).getTime() > now,
              ),
            )
            .slice(0, 3)
            .map((e) => (
              <EventCard key={e.id} event={e} />
            ))}
        </div>
        {isLoading && (
          <Empty
            loading
            title="Loading season"
            copy="Fetching the current FastF1 schedule."
          />
        )}
      </section>
    </div>
  );
}

export function LivePage() {
  const query = useQuery({
    queryKey: ["live"],
    queryFn: () => api<LiveState>("/live"),
    refetchInterval: 60_000,
  });
  if (query.error)
    return (
      <div className="page">
        <ErrorState error={query.error} />
      </div>
    );
  const live = query.data;
  return (
    <div className="page">
      <PageHeader
        eyebrow="Timing centre"
        title="Live"
        copy="An honest view of session availability. Detailed FastF1 timing is published after the session, never fabricated in real time."
        aside={
          <Status kind={live?.state === "in_progress" ? "live" : "neutral"}>
            {live?.state?.replace("_", " ") ?? "checking"}
          </Status>
        }
      />
      <div className="live-layout">
        <section className="live-board">
          <div className="board-top">
            <span>{live?.event?.country ?? "Formula 1"}</span>
            <small>Checked {live ? localDate(live.checked_at) : "now"}</small>
          </div>
          <h2>{live?.event?.name ?? "No active event"}</h2>
          <div className="session-clock">
            <Clock3 />
            <div>
              <span>{live?.session?.name ?? "Next session"}</span>
              <strong>{localDate(live?.session?.starts_at)}</strong>
            </div>
          </div>
          {live?.event && (
            <div className="session-strip">
              {live.event.sessions.map((s) => (
                <div
                  className={s.id === live.session?.id ? "active" : ""}
                  key={s.id}
                >
                  <span>{s.code}</span>
                  <time>
                    {localDate(s.starts_at, {
                      timeStyle: "short",
                      dateStyle: undefined,
                    })}
                  </time>
                </div>
              ))}
            </div>
          )}
        </section>
        <aside className="notice">
          <Radio />
          <h3>What “live” means here</h3>
          <p>{live?.message}</p>
          <dl>
            <div>
              <dt>Schedule state</dt>
              <dd>
                <Check /> Near-live
              </dd>
            </div>
            <div>
              <dt>Lap timing</dt>
              <dd>Post-session</dd>
            </div>
            <div>
              <dt>Telemetry</dt>
              <dd>Post-session</dd>
            </div>
          </dl>
        </aside>
      </div>
      {live?.recent_session && (
        <section className="section">
          <div className="section-title">
            <div>
              <span>Recently completed</span>
              <h2>{live.recent_session.name}</h2>
            </div>
            <Link to={`/sessions/${live.recent_session.id}`}>
              Open analysis <ArrowRight />
            </Link>
          </div>
        </section>
      )}
    </div>
  );
}

export function CalendarPage() {
  const [year, setYear] = useState(currentYear),
    [filter, setFilter] = useState("All");
  const query = useCalendar(year),
    now = Date.now();
  const events = (query.data?.data ?? []).filter(
    (e) =>
      filter === "All" ||
      (filter === "Upcoming") ===
        e.sessions.some(
          (s) => s.starts_at && new Date(s.starts_at).getTime() > now,
        ),
  );
  return (
    <div className="page">
      <PageHeader
        eyebrow="Season programme"
        title="Calendar"
        copy="Every Grand Prix weekend, sprint format and session time in your local timezone."
        aside={<YearSelect year={year} setYear={setYear} />}
      />
      <Tabs
        tabs={["All", "Upcoming", "Completed"]}
        active={filter}
        onChange={setFilter}
      />
      {query.error ? (
        <ErrorState error={query.error} />
      ) : (
        <div className="calendar-list">
          {events.map((e) => (
            <EventCard key={e.id} event={e} />
          ))}
        </div>
      )}
      {query.isLoading && (
        <Empty
          loading
          title="Loading calendar"
          copy={`Syncing ${year} from FastF1.`}
        />
      )}
    </div>
  );
}

export function EventPage() {
  const params = useParams(),
    year = Number(params.season),
    round = Number(params.round);
  const query = useQuery({
    queryKey: ["event", year, round],
    queryFn: () => api<ApiEnvelope<RaceEvent>>(`/events/${year}/${round}`),
  });
  const event = query.data?.data;
  if (query.error)
    return (
      <div className="page">
        <ErrorState error={query.error} />
      </div>
    );
  return (
    <div className="page">
      {event ? (
        <>
          <PageHeader
            eyebrow={`${event.country} / Round ${event.round}`}
            title={event.name}
            copy={`${event.location} · ${event.format?.replaceAll("_", " ")}`}
          />
          <div className="event-detail">
            <TrackMap label={event.location} />
            <div className="session-list">
              <h2>Weekend sessions</h2>
              {event.sessions.map((s) => (
                <Link key={s.id} to={`/sessions/${s.id}`}>
                  <span>{s.code}</span>
                  <div>
                    <b>{s.name}</b>
                    <small>{localDate(s.starts_at)}</small>
                  </div>
                  <ArrowRight />
                </Link>
              ))}
            </div>
          </div>
        </>
      ) : (
        <Empty
          loading
          title="Loading event"
          copy="Fetching weekend schedule."
        />
      )}
    </div>
  );
}

export function StandingsPage() {
  const [year, setYear] = useState(currentYear),
    [kind, setKind] = useState("drivers");
  const query = useQuery({
    queryKey: ["standings", year, kind],
    queryFn: () =>
      api<ApiEnvelope<Record<string, unknown>[]>>(`/standings/${year}/${kind}`),
  });
  const drivers = [
    { key: "position", label: "Pos" },
    {
      key: kind === "drivers" ? "driverCode" : "constructorName",
      label: kind === "drivers" ? "Driver" : "Constructor",
      render: (r: Record<string, unknown>) =>
        kind === "drivers" ? (
          <b>
            {String(
              r.driverCode ?? `${r.givenName ?? ""} ${r.familyName ?? ""}`,
            )}
          </b>
        ) : (
          <b>{String(r.constructorName ?? "—")}</b>
        ),
    },
    { key: "wins", label: "Wins" },
    { key: "points", label: "Points" },
  ];
  return (
    <div className="page">
      <PageHeader
        eyebrow="Championship order"
        title="Standings"
        copy="Driver and constructor standings across the full supported archive."
        aside={<YearSelect year={year} setYear={setYear} />}
      />
      <Tabs
        tabs={["drivers", "constructors"]}
        active={kind}
        onChange={setKind}
      />
      {query.error ? (
        <ErrorState error={query.error} />
      ) : query.isLoading ? (
        <Empty
          loading
          title="Loading standings"
          copy="Reading the championship table."
        />
      ) : (
        <DataTable columns={drivers} rows={query.data?.data ?? []} />
      )}
    </div>
  );
}

function EntityDirectory({ kind }: { kind: "drivers" | "constructors" }) {
  const [year, setYear] = useState(currentYear),
    [search, setSearch] = useState("");
  const query = useQuery({
    queryKey: [kind, year],
    queryFn: () =>
      api<ApiEnvelope<Record<string, unknown>[]>>(`/${kind}?season=${year}`),
  });
  const rows = (query.data?.data ?? []).filter((r) =>
    JSON.stringify(r).toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div className="page">
      <PageHeader
        eyebrow={kind === "drivers" ? "The grid" : "The paddock"}
        title={kind === "drivers" ? "Drivers" : "Teams"}
        copy={`Browse every ${kind === "drivers" ? "driver" : "constructor"} entered in the selected season.`}
        aside={<YearSelect year={year} setYear={setYear} />}
      />
      <label className="search">
        <Search />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={`Search ${kind}`}
        />
      </label>
      {query.error ? (
        <ErrorState error={query.error} />
      ) : (
        <div className="entity-grid">
          {rows.map((r, i) => (
            <article key={String(r.driverId ?? r.constructorId ?? i)}>
              <span>
                {kind === "drivers"
                  ? String(r.driverNumber ?? r.driverCode ?? "—")
                  : String(i + 1).padStart(2, "0")}
              </span>
              <h2>
                {kind === "drivers"
                  ? `${r.givenName ?? ""} ${r.familyName ?? ""}`
                  : String(r.constructorName ?? r.name ?? "Unknown")}
              </h2>
              <p>
                {String(
                  r.driverNationality ??
                    r.constructorNationality ??
                    "Nationality unavailable",
                )}
              </p>
              <div className="entity-code">
                {String(r.driverCode ?? r.constructorId ?? "F1").toUpperCase()}
              </div>
            </article>
          ))}
        </div>
      )}
      {query.isLoading && (
        <Empty
          loading
          title={`Loading ${kind}`}
          copy="Reading the season entry list."
        />
      )}
    </div>
  );
}
export function DriversPage() {
  return <EntityDirectory kind="drivers" />;
}
export function TeamsPage() {
  return <EntityDirectory kind="constructors" />;
}

export function CircuitsPage() {
  const [search, setSearch] = useState("");
  const query = useQuery({
    queryKey: ["circuits"],
    queryFn: () => api<ApiEnvelope<Circuit[]>>("/circuits"),
  });
  const rows = (query.data?.data ?? []).filter((c) =>
    `${c.name} ${c.country} ${c.locality}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className="page">
      <PageHeader
        eyebrow="Track atlas"
        title="Circuits"
        copy="Circuit facts from curated MongoDB metadata, joined with FastF1 event and track information."
      />
      <label className="search">
        <Search />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search circuit or country"
        />
      </label>
      {query.error ? (
        <ErrorState error={query.error} />
      ) : (
        <div className="circuit-grid">
          {rows.map((c) => (
            <Link to={`/circuits/${c.slug}`} key={c.slug}>
              <div className="circuit-map-mini">
                <TrackMap
                  label={c.name}
                  points={c.map_data?.points}
                  rotation={c.map_data?.rotation}
                />
              </div>
              <span>{c.country}</span>
              <h2>{c.name}</h2>
              <p>
                {c.locality ?? "Location unavailable"}{" "}
                {c.length_km && `· ${c.length_km} km`}
              </p>
              <ArrowRight />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function CircuitDetailPage() {
  const { slug } = useParams(),
    [tab, setTab] = useState("Overview");
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["circuit", slug],
    queryFn: () => api<ApiEnvelope<Circuit>>(`/circuits/${slug}`),
    placeholderData: () => {
      const cached = queryClient
        .getQueryData<ApiEnvelope<Circuit[]>>(["circuits"])
        ?.data.find((circuit) => circuit.slug === slug);
      return cached ? { data: cached } : undefined;
    },
  });
  const c = query.data?.data;
  const mapQuery = useQuery({
    queryKey: ["circuit-map", slug],
    queryFn: () => api<any>(`/circuits/${slug}/map`),
    enabled: Boolean(c && !c.map_data),
    refetchInterval: (result) =>
      result.state.data?.availability === "awaiting_data" ? 1000 : false,
  });
  const mapData = c?.map_data ?? mapQuery.data?.data;
  if (query.error)
    return (
      <div className="page">
        <ErrorState error={query.error} />
      </div>
    );
  return (
    <div className="page">
      {c ? (
        <>
          <PageHeader
            eyebrow={`${c.country} / ${c.circuit_type ?? "Circuit"}`}
            title={c.name}
            copy={c.locality}
          />
          <Tabs
            tabs={[
              "Overview",
              "Track Map",
              "Corners & Marshal Points",
              "History",
              "Sessions",
            ]}
            active={tab}
            onChange={setTab}
          />
          <section className="tab-panel">
            {tab === "Overview" && (
              <>
                <div className="metric-grid">
                  <Metric
                    label="Length"
                    value={c.length_km ? `${c.length_km} km` : "—"}
                  />
                  <Metric label="Race laps" value={c.race_laps ?? "—"} />
                  <Metric
                    label="First Grand Prix"
                    value={c.first_grand_prix ?? "—"}
                  />
                  <Metric label="Lap record" value={c.lap_record ?? "—"} />
                </div>
                <TrackMap
                  label={c.name}
                  points={mapData?.points}
                  corners={mapData?.corners}
                  rotation={mapData?.rotation}
                />
              </>
            )}
            {tab === "Track Map" && (
              <TrackMap
                label={c.name}
                points={mapData?.points}
                corners={mapData?.corners}
                rotation={mapData?.rotation}
              />
            )}{" "}
            {tab === "Corners & Marshal Points" &&
              (mapData?.points ? (
                <>
                  <div className="metric-grid marker-metrics">
                    <Metric
                      label="Corners"
                      value={mapData.corners?.length ?? 0}
                    />
                    <Metric
                      label="Marshal lights"
                      value={mapData.marshal_lights?.length ?? 0}
                    />
                    <Metric
                      label="Marshal sectors"
                      value={mapData.marshal_sectors?.length ?? 0}
                    />
                    <Metric
                      label="Map rotation"
                      value={`${Math.round(mapData.rotation ?? 0)}°`}
                    />
                  </div>
                  <TrackMap
                    label={c.name}
                    points={mapData.points}
                    corners={mapData.corners}
                    rotation={mapData.rotation}
                  />
                </>
              ) : (
                <Empty
                  loading={mapQuery.isFetching}
                  title="Building circuit map"
                  copy={
                    mapQuery.data?.unavailable_reason ??
                    "Loading a recent reference lap and official circuit markers."
                  }
                />
              ))}
            {tab === "History" && (
              <Empty
                title="History index not synced"
                copy="Run a historical season sync from Operations to connect previous events at this circuit."
              />
            )}
            {tab === "Sessions" && (
              <Empty
                title="Choose from the calendar"
                copy="Open a race weekend to browse every available session at this circuit."
              />
            )}
          </section>
        </>
      ) : (
        <Empty
          loading
          title="Loading circuit"
          copy="Reading curated circuit details."
        />
      )}
    </div>
  );
}

type LapRecord = {
  Driver?: string;
  Team?: string;
  Position?: number;
  LapNumber?: number;
  LapTime?: number;
  Sector1Time?: number;
  Sector2Time?: number;
  Sector3Time?: number;
  Compound?: string;
  TyreLife?: number;
  Stint?: number;
  PitInTime?: number;
  PitOutTime?: number;
  TrackStatus?: string;
  IsAccurate?: boolean;
  Deleted?: boolean;
  DeletedReason?: string;
};

function LapAnalysis({ laps }: { laps: LapRecord[] }) {
  const drivers = useMemo(
    () =>
      [...new Set(laps.map((lap) => lap.Driver).filter(Boolean))] as string[],
    [laps],
  );
  const compounds = useMemo(
    () =>
      [...new Set(laps.map((lap) => lap.Compound).filter(Boolean))] as string[],
    [laps],
  );
  const fastestDriver = useMemo(
    () =>
      laps
        .filter((lap) => lap.LapTime != null && lap.IsAccurate && !lap.Deleted)
        .sort(
          (left, right) => (left.LapTime as number) - (right.LapTime as number),
        )[0]?.Driver,
    [laps],
  );
  const [driver, setDriver] = useState(fastestDriver ?? drivers[0] ?? "ALL");
  const [compound, setCompound] = useState("ALL");
  const [accurateOnly, setAccurateOnly] = useState(true);
  useEffect(() => {
    if (driver !== "ALL" && !drivers.includes(driver))
      setDriver(fastestDriver ?? drivers[0] ?? "ALL");
  }, [driver, drivers, fastestDriver]);
  const filtered = useMemo(
    () =>
      laps.filter(
        (lap) =>
          (driver === "ALL" || lap.Driver === driver) &&
          (compound === "ALL" || lap.Compound === compound) &&
          (!accurateOnly || (lap.IsAccurate && !lap.Deleted)),
      ),
    [laps, driver, compound, accurateOnly],
  );
  const timed = filtered.filter((lap) => lap.LapTime != null);
  const fastest = timed.length
    ? Math.min(...timed.map((lap) => lap.LapTime as number))
    : null;
  const average = timed.length
    ? timed.reduce((sum, lap) => sum + (lap.LapTime as number), 0) /
      timed.length
    : null;
  const seriesDrivers = useMemo(
    () => (driver === "ALL" ? drivers : [driver]),
    [driver, drivers],
  );
  const chartOption = useMemo(
    () => ({
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { data: seriesDrivers },
      grid: { left: 64, right: 24, top: 48, bottom: 48 },
      xAxis: { type: "value", name: "Lap", minInterval: 1 },
      yAxis: {
        type: "value",
        name: "Lap time",
        scale: true,
        axisLabel: {
          formatter: (value: number) => `${(value / 1000).toFixed(1)}s`,
        },
      },
      series: seriesDrivers.map((code) => ({
        name: code,
        type: "line",
        showSymbol: driver !== "ALL",
        symbolSize: 5,
        connectNulls: false,
        data: filtered
          .filter((lap) => lap.Driver === code && lap.LapTime != null)
          .map((lap) => [lap.LapNumber, lap.LapTime]),
      })),
    }),
    [driver, filtered, seriesDrivers],
  );
  return (
    <div className="lap-analysis">
      <div className="lap-toolbar">
        <label>
          Driver
          <select
            value={driver}
            onChange={(event) => setDriver(event.target.value)}
          >
            <option value="ALL">All drivers</option>
            {drivers.map((code) => (
              <option key={code}>{code}</option>
            ))}
          </select>
        </label>
        <label>
          Compound
          <select
            value={compound}
            onChange={(event) => setCompound(event.target.value)}
          >
            <option value="ALL">All compounds</option>
            {compounds.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label className="check-control">
          <input
            type="checkbox"
            checked={accurateOnly}
            onChange={(event) => setAccurateOnly(event.target.checked)}
          />
          Accurate laps only
        </label>
      </div>
      <div className="lap-summary">
        <Metric
          label="Visible laps"
          value={filtered.length}
          detail={`${timed.length} timed`}
        />
        <Metric
          label="Fastest"
          value={duration(fastest)}
          detail={driver === "ALL" ? "Filtered field" : driver}
        />
        <Metric
          label="Average"
          value={duration(average)}
          detail="Accurate timed laps"
        />
        <Metric
          label="Stints"
          value={
            new Set(filtered.map((lap) => `${lap.Driver}-${lap.Stint}`)).size
          }
          detail={`${compounds.length} compounds`}
        />
      </div>
      <div className="lap-chart">
        <Suspense
          fallback={
            <Empty
              loading
              title="Loading lap chart"
              copy="Preparing lap-by-lap pace."
            />
          }
        >
          <TelemetryChart option={chartOption} height={360} />
        </Suspense>
      </div>
      <div className="table-wrap lap-table">
        <table>
          <thead>
            <tr>
              <th>Lap</th>
              <th>Driver</th>
              <th>Position</th>
              <th>Lap time</th>
              <th>S1</th>
              <th>S2</th>
              <th>S3</th>
              <th>Tyre</th>
              <th>Age</th>
              <th>Stint</th>
              <th>Event</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((lap, index) => (
              <tr
                key={`${lap.Driver}-${lap.LapNumber}-${index}`}
                className={lap.Deleted ? "deleted-lap" : ""}
              >
                <td>
                  <b>{lap.LapNumber ?? "—"}</b>
                </td>
                <td>
                  <strong>{lap.Driver ?? "—"}</strong>
                  <small>{lap.Team}</small>
                </td>
                <td>{lap.Position ?? "—"}</td>
                <td className={lap.LapTime === fastest ? "fastest-time" : ""}>
                  {duration(lap.LapTime)}
                </td>
                <td>{duration(lap.Sector1Time)}</td>
                <td>{duration(lap.Sector2Time)}</td>
                <td>{duration(lap.Sector3Time)}</td>
                <td>
                  <span
                    className={`compound compound-${(lap.Compound ?? "unknown").toLowerCase()}`}
                  >
                    {lap.Compound ?? "—"}
                  </span>
                </td>
                <td>
                  {lap.TyreLife != null ? `${Math.round(lap.TyreLife)}L` : "—"}
                </td>
                <td>{lap.Stint ?? "—"}</td>
                <td>
                  {lap.PitInTime != null || lap.PitOutTime != null ? (
                    <Status kind="warn">PIT</Status>
                  ) : lap.Deleted ? (
                    <span title={lap.DeletedReason}>Deleted</span>
                  ) : lap.TrackStatus !== "1" ? (
                    <span>Flag {lap.TrackStatus}</span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ResultsAnalysis({ rows }: { rows: Record<string, unknown>[] }) {
  return (
    <DataTable
      columns={[
        {
          key: "Position",
          label: "Pos",
          render: (row) => <b>{String(row.Position ?? "—")}</b>,
        },
        {
          key: "Abbreviation",
          label: "Driver",
          render: (row) => (
            <>
              <strong>{String(row.Abbreviation ?? "")}</strong>
              <small className="table-subline">
                {String(row.FullName ?? "")}
              </small>
            </>
          ),
        },
        { key: "TeamName", label: "Team" },
        { key: "GridPosition", label: "Grid" },
        {
          key: "Time",
          label: "Time / Gap",
          render: (row) => duration(row.Time as number | null),
        },
        {
          key: "Q1",
          label: "Q1",
          render: (row) => duration(row.Q1 as number | null),
        },
        {
          key: "Q2",
          label: "Q2",
          render: (row) => duration(row.Q2 as number | null),
        },
        {
          key: "Q3",
          label: "Q3",
          render: (row) => duration(row.Q3 as number | null),
        },
        { key: "Points", label: "Points" },
        { key: "Status", label: "Status" },
      ]}
      rows={rows}
    />
  );
}

export function SessionPage() {
  const { sessionId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState(searchParams.get("tab") ?? "Overview"),
    [drivers, setDrivers] = useState(""),
    [channels, setChannels] = useState("Speed,RPM,Throttle,Brake,nGear,DRS"),
    [plotChannel, setPlotChannel] = useState("Speed");
  const kind =
    tab === "Overview"
      ? "summary"
      : tab === "Race Control"
        ? "race-control"
        : tab.toLowerCase();
  const path =
    kind === "telemetry"
      ? `/sessions/${sessionId}/telemetry?drivers=${encodeURIComponent(drivers)}&laps=fastest&channels=${encodeURIComponent(channels)}`
      : `/sessions/${sessionId}/${kind}`;
  const summaryQuery = useQuery({
    queryKey: ["session-summary", sessionId],
    queryFn: () => api<any>(`/sessions/${sessionId}/summary`),
    refetchInterval: (result) =>
      ["queued", "running"].includes(result.state.data?.status) ? 1500 : false,
  });
  const trackQuery = useQuery({
    queryKey: ["session", sessionId, "track"],
    queryFn: () => api<any>(`/sessions/${sessionId}/track`),
    enabled: tab === "Overview" || tab === "Track",
    refetchInterval: (result) =>
      result.state.data?.availability === "awaiting_data" ? 1000 : false,
  });
  const detailQuery = useQuery({
    queryKey: ["session", sessionId, kind, drivers, channels],
    queryFn: () => api<any>(path),
    enabled: kind !== "summary" && kind !== "track",
    refetchInterval: (result) =>
      ["queued", "running"].includes(result.state.data?.status) ? 1500 : false,
  });
  const query =
    kind === "summary"
      ? summaryQuery
      : kind === "track"
        ? trackQuery
        : detailQuery;
  const payload = query.data;
  const data = payload?.data;
  const sessionSummary = summaryQuery.data?.data;
  const queued = payload?.status === "queued" || payload?.status === "running";
  const sessionState = payload?.availability as string | undefined;
  const waitingForSession = [
    "scheduled",
    "in_progress",
    "awaiting_data",
  ].includes(sessionState ?? "");
  const telemetryOption = useMemo(() => {
    const traces = data?.traces ?? [];
    return {
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { data: traces.map((t: any) => t.driver) },
      grid: { left: 58, right: 20, top: 40, bottom: 45 },
      xAxis: { type: "value", name: "Distance m" },
      yAxis: {
        type: "value",
        name: plotChannel === "Delta" ? "Delta ms" : plotChannel,
      },
      series: traces
        .filter(
          (_: any, index: number) => plotChannel !== "Delta" || index === 1,
        )
        .map((t: any) => ({
          name: t.driver,
          type: "line",
          showSymbol: false,
          data: t.points
            .filter((p: any) => p[plotChannel] != null)
            .map((p: any) => [p.Distance, p[plotChannel]]),
        })),
    };
  }, [data, plotChannel]);
  if (payload?.status === "failed")
    return (
      <div className="page">
        <PageHeader
          eyebrow="Session analysis"
          title={sessionId?.replaceAll("-", " / ") ?? "Session"}
          copy="FastF1 timing, strategy, conditions and car data."
          aside={<Status kind="warn">failed</Status>}
        />
        <Empty
          title="Session processing failed"
          copy={
            payload.error ??
            "The upstream session data could not be processed. An operator can retry this job."
          }
        />
      </div>
    );
  return (
    <div className="page">
      <PageHeader
        eyebrow={
          sessionSummary
            ? `${sessionSummary.country} / ${sessionSummary.location}`
            : "Session analysis"
        }
        title={
          sessionSummary
            ? `${sessionSummary.event} / ${sessionSummary.name}`
            : (sessionId?.replaceAll("-", " / ") ?? "Session")
        }
        copy={
          sessionSummary?.date
            ? `${localDate(sessionSummary.date)} · FastF1 timing and car data`
            : "FastF1 timing, strategy, conditions and car data."
        }
        aside={
          <Status
            kind={queued || waitingForSession ? "warn" : data ? "good" : "neutral"}
          >
            {queued
              ? "processing"
              : waitingForSession
                ? sessionState?.replaceAll("_", " ")
                : data
                  ? "available"
                  : "requesting"}
          </Status>
        }
      />
      <Tabs
        tabs={[
          "Overview",
          "Results",
          "Laps",
          "Telemetry",
          "Strategy",
          "Weather",
          "Race Control",
          "Track",
        ]}
        active={tab}
        onChange={(nextTab) => {
          setTab(nextTab);
          setSearchParams({ tab: nextTab });
        }}
      />
      {tab === "Telemetry" && !waitingForSession && (
        <div className="telemetry-controls">
          <label>
            Drivers
            <input
              value={drivers}
              onChange={(e) => setDrivers(e.target.value.toUpperCase())}
              placeholder="VER,NOR"
              maxLength={7}
            />
          </label>
          <label>
            Channels
            <select
              value={channels}
              onChange={(e) => setChannels(e.target.value)}
            >
              <option>Speed,RPM,Throttle,Brake,nGear,DRS</option>
              <option>Speed,Throttle,Brake</option>
              <option>Speed,RPM,nGear</option>
            </select>
          </label>
          <label>
            Chart
            <select
              value={plotChannel}
              onChange={(event) => setPlotChannel(event.target.value)}
            >
              {(
                data?.channels ?? [
                  "Speed",
                  "RPM",
                  "Throttle",
                  "Brake",
                  "nGear",
                  "DRS",
                ]
              ).map((channel: string) => (
                <option key={channel}>{channel}</option>
              ))}
            </select>
          </label>
        </div>
      )}
      {query.error ? (
        <ErrorState error={query.error} />
      ) : queued ? (
        <Empty
          loading
          title="Processing session"
          copy="The worker is loading and caching this session. This page refreshes when the job completes."
        />
      ) : waitingForSession ? (
        <Empty
          title={
            sessionState === "scheduled"
              ? "Session has not started"
              : sessionState === "in_progress"
                ? "Session in progress"
                : "Timing data is being published"
          }
          copy={payload.unavailable_reason}
        />
      ) : payload?.availability === "unavailable" ? (
        <Empty title="Detail unavailable" copy={payload.unavailable_reason} />
      ) : tab === "Telemetry" && data ? (
        <div className="chart-panel">
          <Suspense
            fallback={
              <Empty
                loading
                title="Loading chart"
                copy="Preparing the telemetry renderer."
              />
            }
          >
            <TelemetryChart option={telemetryOption} />
          </Suspense>
          <div className="trace-meta">
            {data.traces?.map((t: any) => (
              <Metric
                key={t.driver}
                label={t.driver}
                value={duration(t.lap_time)}
                detail={`Fastest lap ${t.lap}`}
              />
            ))}
          </div>
        </div>
      ) : tab === "Overview" && data ? (
        <div className="session-overview">
          <div className="metric-grid">
            {Object.entries(data)
              .slice(0, 8)
              .map(([k, v]) => (
                <Metric
                  key={k}
                  label={k.replaceAll("_", " ")}
                  value={Array.isArray(v) ? v.length : String(v ?? "â€”")}
                />
              ))}
          </div>
          <TrackMap
            label={sessionSummary?.location ?? sessionId}
            points={trackQuery.data?.data?.points}
            corners={trackQuery.data?.data?.corners}
            rotation={trackQuery.data?.data?.rotation}
          />
        </div>
      ) : tab === "Track" && data ? (
        <TrackMap
          label={sessionId}
          points={data.points}
          corners={data.corners}
          rotation={data.rotation}
        />
      ) : tab === "Laps" && Array.isArray(data) ? (
        <LapAnalysis laps={data} />
      ) : tab === "Results" && Array.isArray(data) ? (
        <ResultsAnalysis rows={data} />
      ) : Array.isArray(data) ? (
        <DataTable
          columns={Object.keys(data[0] ?? {})
            .slice(0, 8)
            .map((k) => ({ key: k, label: k.replaceAll("_", " ") }))}
          rows={data}
        />
      ) : data ? (
        <div className="metric-grid">
          {Object.entries(data)
            .slice(0, 8)
            .map(([k, v]) => (
              <Metric
                key={k}
                label={k.replaceAll("_", " ")}
                value={Array.isArray(v) ? v.length : String(v ?? "—")}
              />
            ))}
        </div>
      ) : (
        <Empty
          loading
          title="Requesting session"
          copy="Checking the derived-data cache."
        />
      )}
    </div>
  );
}

export function AdminPage() {
  const [username, setUsername] = useState("admin"),
    [password, setPassword] = useState(""),
    [csrf, setCsrf] = useState(sessionStorage.getItem("csrf") ?? ""),
    [message, setMessage] = useState("");
  const me = useQuery({
    queryKey: ["admin-me", csrf],
    queryFn: () =>
      api<{ authenticated: boolean; username: string }>("/admin/me"),
    retry: false,
  });
  const cache = useQuery({
    queryKey: ["admin-cache"],
    queryFn: () => api<{ path: string; size_bytes: number }>("/admin/cache"),
    enabled: !!me.data?.authenticated,
  });
  const jobs = useQuery({
    queryKey: ["admin-jobs"],
    queryFn: () => api<ApiEnvelope<Record<string, unknown>[]>>("/admin/jobs"),
    enabled: !!me.data?.authenticated,
    refetchInterval: 5000,
  });
  async function login(e: FormEvent) {
    e.preventDefault();
    try {
      const result = await api<{ csrf_token: string }>("/admin/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      sessionStorage.setItem("csrf", result.csrf_token);
      setCsrf(result.csrf_token);
      setMessage("Signed in.");
    } catch (err) {
      setMessage((err as Error).message);
    }
  }
  async function sync(kind: string) {
    const payload =
      kind === "season"
        ? { kind, season: currentYear }
        : kind === "backfill" || kind === "full-backfill"
          ? {
              kind: "backfill",
              start: 1950,
              end: currentYear,
              include_telemetry: kind === "full-backfill",
            }
          : { kind: "circuits", season: currentYear };
    const result = await api<Job>("/admin/sync", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      body: JSON.stringify(payload),
    });
    setMessage(`Queued ${result.job_id}`);
  }
  return (
    <div className="page">
      <PageHeader
        eyebrow="Private tools"
        title="Operations"
        copy="Ingestion, cache health and curated data controls."
        aside={
          <Status kind={me.data?.authenticated ? "good" : "neutral"}>
            {me.data?.authenticated ? "authenticated" : "locked"}
          </Status>
        }
      />
      {!me.data?.authenticated ? (
        <form className="login-panel" onSubmit={login}>
          <LockKeyhole />
          <h2>Operator sign in</h2>
          <label>
            Username
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button className="button primary">Sign in</button>
          {message && <p>{message}</p>}
        </form>
      ) : (
        <>
          <div className="ops-grid">
            <button onClick={() => sync("season")}>
              <CalendarDays />
              <b>Sync current season</b>
              <span>Schedule and session index</span>
            </button>
            <button onClick={() => sync("circuits")}>
              <Map />
              <b>Sync circuits</b>
              <span>Jolpica identities and locations</span>
            </button>
            <button onClick={() => sync("backfill")}>
              <Database />
              <b>Queue historical index</b>
              <span>Schedules, rosters and standings from 1950</span>
            </button>
            <button onClick={() => sync("full-backfill")}>
              <Activity />
              <b>Queue full timing archive</b>
              <span>2018+ laps, maps and per-lap telemetry</span>
            </button>
            <button onClick={() => cache.refetch()}>
              <Gauge />
              <b>Cache health</b>
              <span>
                {cache.data
                  ? `${(cache.data.size_bytes / 1048576).toFixed(1)} MB`
                  : "Check persistent volume"}
              </span>
            </button>
            <button onClick={() => jobs.refetch()}>
              <Wrench />
              <b>Refresh jobs</b>
              <span>{jobs.data?.data.length ?? 0} recent ingestion jobs</span>
            </button>
          </div>
          {message && (
            <div className="operation-message">
              <Activity />
              {message}
            </div>
          )}
          <CircuitEditor csrf={csrf} setMessage={setMessage} />
          <section className="section">
            <div className="section-title">
              <div>
                <span>Worker queue</span>
                <h2>Recent jobs</h2>
              </div>
            </div>
            <DataTable
              columns={[
                { key: "kind", label: "Kind" },
                { key: "key", label: "Key" },
                { key: "status", label: "Status" },
                { key: "progress", label: "Progress" },
                { key: "attempts", label: "Attempts" },
                { key: "updated_at", label: "Updated" },
              ]}
              rows={jobs.data?.data ?? []}
            />
          </section>
        </>
      )}
    </div>
  );
}

function CircuitEditor({
  csrf,
  setMessage,
}: {
  csrf: string;
  setMessage: (message: string) => void;
}) {
  const query = useQuery({
    queryKey: ["circuits"],
    queryFn: () => api<ApiEnvelope<Circuit[]>>("/circuits"),
  });
  const [slug, setSlug] = useState(""),
    [form, setForm] = useState<Record<string, string>>({});
  const circuit = query.data?.data.find((item) => item.slug === slug);
  useEffect(() => {
    if (circuit)
      setForm({
        length_km: String(circuit.length_km ?? ""),
        race_laps: String(circuit.race_laps ?? ""),
        lap_record: circuit.lap_record ?? "",
        first_grand_prix: String(circuit.first_grand_prix ?? ""),
        circuit_type: circuit.circuit_type ?? "",
        source_url: circuit.source_url ?? "",
      });
  }, [circuit]);
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!slug) return;
    const numeric = ["length_km", "race_laps", "first_grand_prix"];
    const payload = Object.fromEntries(
      Object.entries(form).map(([key, value]) => [
        key,
        numeric.includes(key) ? (value ? Number(value) : null) : value || null,
      ]),
    );
    await api(`/admin/circuits/${slug}`, {
      method: "PUT",
      headers: { "X-CSRF-Token": csrf },
      body: JSON.stringify(payload),
    });
    setMessage(`Saved metadata for ${circuit?.name}`);
    query.refetch();
  }
  return (
    <section className="section">
      <div className="section-title">
        <div>
          <span>Curated MongoDB data</span>
          <h2>Circuit metadata</h2>
        </div>
      </div>
      <form className="metadata-form" onSubmit={save}>
        <label>
          Circuit
          <select value={slug} onChange={(e) => setSlug(e.target.value)}>
            <option value="">Select circuit</option>
            {query.data?.data.map((c) => (
              <option value={c.slug} key={c.slug}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {[
          ["length_km", "Length km"],
          ["race_laps", "Race laps"],
          ["lap_record", "Lap record"],
          ["first_grand_prix", "First Grand Prix"],
          ["circuit_type", "Circuit type"],
          ["source_url", "Source URL"],
        ].map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              value={form[key] ?? ""}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })}
            />
          </label>
        ))}
        <button className="button primary" disabled={!slug}>
          Save metadata
        </button>
      </form>
    </section>
  );
}
