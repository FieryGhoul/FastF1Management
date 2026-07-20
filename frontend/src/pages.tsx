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
import { api, duration, formatValue, localDate } from "./api";
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

function compactEta(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return "Calculating";
  if (seconds <= 0) return "Complete";
  const hours = Math.ceil(seconds / 3600);
  if (hours < 24) return `About ${hours}h`;
  const days = Math.floor(hours / 24);
  return `About ${days}d ${hours % 24}h`;
}

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
      (next?.circuit_slug && circuit.slug === next.circuit_slug) ||
        (location &&
          (circuit.locality?.toLowerCase() === location ||
            circuit.name.toLowerCase().includes(location))),
    );
  });
  const homeMap = useQuery({
    queryKey: ["circuit-map", nextCircuit?.slug],
    queryFn: () =>
      api<{
        availability: string;
        data: NonNullable<Circuit["map_data"]>;
      }>(`/circuits/${nextCircuit!.slug}/map`),
    enabled: Boolean(nextCircuit?.slug),
  });
  const homeMapData = homeMap.data?.data;
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
  const positionedCornerCount = new Set(
    (homeMapData?.corners ?? [])
      .map((corner: { Number?: number }) => corner.Number)
      .filter((number: number | undefined) => number != null),
  ).size;
  const cornerCount = positionedCornerCount || nextCircuit?.corner_count || 0;
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
            emptyMessage={
              circuits.error || homeMap.error
                ? "The circuit service did not respond. Refresh to retry."
                : circuits.isFetching || homeMap.isFetching
                  ? "Loading the stored FastF1 circuit outline."
                  : "No stored outline is available for this circuit yet."
            }
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
      ) : query.isLoading ? (
        <Empty
          loading
          title="Loading circuit atlas"
          copy="Reading every stored circuit outline."
        />
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
  const eventMap = useQuery({
    queryKey: ["circuit-map", event?.circuit_slug],
    queryFn: () => api<any>(`/circuits/${event!.circuit_slug}/map`),
    enabled: Boolean(event?.circuit_slug),
  });
  const eventMapData = eventMap.data?.data;
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
            <div>
              <TrackMap
                label={event.location}
                points={eventMapData?.points}
                corners={eventMapData?.corners}
                rotation={eventMapData?.rotation}
                emptyMessage={
                  eventMap.error
                    ? "The stored circuit outline could not be loaded."
                    : eventMap.isFetching
                      ? "Loading the stored circuit outline."
                      : "No canonical circuit map is linked to this event."
                }
              />
              {event.circuit_slug && (
                <Link
                  className="circuit-map-link"
                  to={`/circuits/${event.circuit_slug}`}
                >
                  Open circuit details <ArrowRight />
                </Link>
              )}
            </div>
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
    [kind, setKind] = useState("drivers"),
    [showAllFields, setShowAllFields] = useState(false);
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
        <div className="data-view">
          <div className="data-view-toolbar">
            <span>
              {showAllFields
                ? `Showing every stored standings field (${allDataColumns(query.data?.data ?? []).length})`
                : "Focused championship view"}
            </span>
            <button
              type="button"
              className="button ghost field-toggle"
              onClick={() => setShowAllFields((current) => !current)}
            >
              {showAllFields ? "Focused columns" : "All stored fields"}
            </button>
          </div>
          <DataTable
            columns={
              showAllFields
                ? allDataColumns(query.data?.data ?? [])
                : drivers
            }
            rows={query.data?.data ?? []}
          />
        </div>
      )}
    </div>
  );
}

function EntityDirectory({ kind }: { kind: "drivers" | "constructors" }) {
  const [year, setYear] = useState(currentYear),
    [search, setSearch] = useState(""),
    [showAllFields, setShowAllFields] = useState(false);
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
      <div className="directory-tools">
        <label className="search">
          <Search />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={`Search ${kind}`}
          />
        </label>
        <button
          type="button"
          className="button ghost field-toggle"
          onClick={() => setShowAllFields((current) => !current)}
        >
          {showAllFields ? "Card view" : "All stored fields"}
        </button>
      </div>
      {query.error ? (
        <ErrorState error={query.error} />
      ) : showAllFields ? (
        <DataTable columns={allDataColumns(rows)} rows={rows} />
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
    queryKey: ["circuits", "with-maps"],
    queryFn: () => api<ApiEnvelope<Circuit[]>>("/circuits?include_maps=true"),
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
        .getQueryData<ApiEnvelope<Circuit[]>>(["circuits", "with-maps"])
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
  const circuitEvents = c?.events ?? [];
  const circuitSessions = circuitEvents.flatMap((event) =>
    event.sessions.map((session) => ({
      id: session.id,
      season: event.season,
      round: event.round,
      event: event.name,
      session: session.name,
      code: session.code,
      date: localDate(session.starts_at),
    })),
  );
  const circuitMetadata = c
    ? [
        Object.fromEntries(
          Object.entries(c as unknown as Record<string, unknown>).filter(
            ([key]) => !["map_data", "events"].includes(key),
          ),
        ),
      ]
    : [];
  const mapCornerCount = new Set(
    (mapData?.corners ?? [])
      .map((corner: { Number?: number }) => corner.Number)
      .filter((number: number | undefined) => number != null),
  ).size || c?.corner_count || 0;
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
              "Metadata",
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
                  <Metric label="Turns" value={c.corner_count ?? "—"} />
                  <Metric
                    label="First Grand Prix"
                    value={c.first_grand_prix ?? "—"}
                  />
                  <Metric label="Direction" value={c.direction ?? "—"} />
                </div>
                <TrackMap
                  label={c.name}
                  points={mapData?.points}
                  corners={mapData?.corners}
                  rotation={mapData?.rotation}
                  emptyMessage={
                    mapQuery.error
                      ? "The stored outline could not be loaded."
                      : "Loading the stored FastF1 circuit outline."
                  }
                />
              </>
            )}
            {tab === "Track Map" && (
              <TrackMap
                label={c.name}
                points={mapData?.points}
                corners={mapData?.corners}
                rotation={mapData?.rotation}
                emptyMessage={
                  mapQuery.error
                    ? "The stored outline could not be loaded."
                    : "Loading the stored FastF1 circuit outline."
                }
              />
            )}{" "}
            {tab === "Corners & Marshal Points" &&
              (mapData?.points ? (
                <>
                  <div className="metric-grid marker-metrics">
                    <Metric
                      label="Corners"
                      value={mapCornerCount}
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
              circuitEvents.length ? (
                <>
                  <div className="metric-grid marker-metrics">
                    <Metric label="Grands Prix" value={c.event_count ?? circuitEvents.length} />
                    <Metric
                      label="First stored"
                      value={circuitEvents.at(-1)?.season ?? "—"}
                    />
                    <Metric
                      label="Latest stored"
                      value={circuitEvents[0]?.season ?? "—"}
                    />
                    <Metric label="Sessions" value={c.session_count ?? circuitSessions.length} />
                  </div>
                  <div className="event-grid">
                    {circuitEvents.map((event) => (
                      <EventCard key={event.id} event={event} compact />
                    ))}
                  </div>
                </>
              ) : (
                <Empty
                  title="No linked race weekends"
                  copy="No stored event currently references this circuit."
                />
              )
            )}
            {tab === "Sessions" && (
              circuitSessions.length ? (
                <DataTable
                  columns={[
                    { key: "season", label: "Season" },
                    { key: "round", label: "Round" },
                    { key: "event", label: "Grand Prix" },
                    { key: "session", label: "Session" },
                    { key: "code", label: "Code" },
                    { key: "date", label: "Date" },
                  ]}
                  rows={circuitSessions}
                />
              ) : (
                <Empty
                  title="No linked sessions"
                  copy="No stored sessions currently reference this circuit."
                />
              )
            )}
            {tab === "Metadata" && (
              <div className="data-view">
                <div className="data-view-toolbar">
                  <span>
                    Complete circuit identity, coordinates, provenance, and
                    catalog metadata
                  </span>
                </div>
                <DataTable
                  columns={allDataColumns(circuitMetadata)}
                  rows={circuitMetadata}
                />
              </div>
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

function fieldLabel(key: string) {
  return key
    .replaceAll("_", " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/^./, (letter) => letter.toUpperCase());
}

function allDataColumns(rows: Record<string, unknown>[]) {
  const keys: string[] = [];
  const seen = new Set<string>();
  rows.forEach((row) =>
    Object.keys(row).forEach((key) => {
      if (!seen.has(key)) {
        seen.add(key);
        keys.push(key);
      }
    }),
  );
  return keys.map((key) => ({ key, label: fieldLabel(key) }));
}

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
  const [showAllFields, setShowAllFields] = useState(false);
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
        <button
          type="button"
          className="button ghost field-toggle"
          onClick={() => setShowAllFields((current) => !current)}
        >
          {showAllFields ? "Focused columns" : `All ${allDataColumns(laps as unknown as Record<string, unknown>[]).length} fields`}
        </button>
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
      {showAllFields ? (
        <DataTable
          columns={allDataColumns(
            filtered as unknown as Record<string, unknown>[],
          )}
          rows={filtered as unknown as Record<string, unknown>[]}
        />
      ) : (
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
      )}
    </div>
  );
}

function ResultsAnalysis({ rows }: { rows: Record<string, unknown>[] }) {
  const [showAllFields, setShowAllFields] = useState(false);
  const focusedColumns = [
    {
      key: "Position",
      label: "Pos",
      render: (row: Record<string, unknown>) => (
        <b>{formatValue(row.Position, "Position")}</b>
      ),
    },
    {
      key: "Abbreviation",
      label: "Driver",
      render: (row: Record<string, unknown>) => (
        <>
          <strong>{formatValue(row.Abbreviation, "Abbreviation")}</strong>
          <small className="table-subline">
            {formatValue(row.FullName, "FullName")}
          </small>
        </>
      ),
    },
    { key: "TeamName", label: "Team" },
    { key: "GridPosition", label: "Grid" },
    {
      key: "Time",
      label: "Time / Gap",
      render: (row: Record<string, unknown>) =>
        duration(row.Time as number | null),
    },
    {
      key: "Q1",
      label: "Q1",
      render: (row: Record<string, unknown>) =>
        duration(row.Q1 as number | null),
    },
    {
      key: "Q2",
      label: "Q2",
      render: (row: Record<string, unknown>) =>
        duration(row.Q2 as number | null),
    },
    {
      key: "Q3",
      label: "Q3",
      render: (row: Record<string, unknown>) =>
        duration(row.Q3 as number | null),
    },
    { key: "Points", label: "Points" },
    { key: "Status", label: "Status" },
  ];
  return (
    <div className="data-view">
      <div className="data-view-toolbar">
        <span>
          {showAllFields
            ? `Showing every stored result field (${allDataColumns(rows).length})`
            : "Focused classification view"}
        </span>
        <button
          type="button"
          className="button ghost field-toggle"
          onClick={() => setShowAllFields((current) => !current)}
        >
          {showAllFields ? "Focused columns" : "All stored fields"}
        </button>
      </div>
      <DataTable
        columns={showAllFields ? allDataColumns(rows) : focusedColumns}
        rows={rows}
      />
    </div>
  );
}

export function SessionPage() {
  const { sessionId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState(searchParams.get("tab") ?? "Overview"),
    [drivers, setDrivers] = useState(""),
    [lapNumber, setLapNumber] = useState(""),
    [channels, setChannels] = useState("Speed,RPM,Throttle,Brake,nGear,DRS"),
    [plotChannel, setPlotChannel] = useState("Speed"),
    [stream, setStream] = useState<"merged" | "car" | "position">("merged");
  const kind =
    tab === "Overview"
      ? "summary"
      : tab === "Race Control"
        ? "race-control"
        : tab.toLowerCase();
  const path =
    kind === "telemetry"
      ? `/sessions/${sessionId}/telemetry?drivers=${encodeURIComponent(drivers)}&laps=${encodeURIComponent(lapNumber || "fastest")}&channels=${encodeURIComponent(channels)}&stream=${stream}`
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
    queryKey: [
      "session",
      sessionId,
      kind,
      drivers,
      lapNumber,
      channels,
      stream,
    ],
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
  const chartChannels = useMemo(() => {
    const traces = data?.traces ?? [];
    return (data?.channels ?? []).filter((channel: string) =>
      traces.some((trace: any) =>
        trace.points?.some((point: any) => typeof point[channel] === "number"),
      ),
    );
  }, [data]);
  const activePlotChannel = chartChannels.includes(plotChannel)
    ? plotChannel
    : (chartChannels[0] ?? plotChannel);
  const xChannel = useMemo(() => {
    const points = (data?.traces ?? []).flatMap((trace: any) => trace.points ?? []);
    if (points.some((point: any) => typeof point.Distance === "number"))
      return "Distance";
    if (points.some((point: any) => typeof point.Time === "number")) return "Time";
    return "SessionTime";
  }, [data]);
  const telemetryOption = useMemo(() => {
    const traces = data?.traces ?? [];
    return {
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { data: traces.map((t: any) => t.driver) },
      grid: { left: 58, right: 20, top: 40, bottom: 45 },
      xAxis: {
        type: "value",
        name: xChannel === "Distance" ? "Distance m" : `${xChannel} ms`,
      },
      yAxis: {
        type: "value",
        name:
          activePlotChannel === "Delta" ? "Delta ms" : activePlotChannel,
      },
      series: traces
        .filter(
          (_: any, index: number) =>
            activePlotChannel !== "Delta" || index === 1,
        )
        .map((t: any) => ({
          name: t.driver,
          type: "line",
          showSymbol: false,
          data: t.points
            .filter(
              (p: any) =>
                typeof p[xChannel] === "number" &&
                typeof p[activePlotChannel] === "number",
            )
            .map((p: any) => [p[xChannel], p[activePlotChannel]]),
        })),
    };
  }, [activePlotChannel, data, xChannel]);
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
            Lap
            <input
              type="number"
              min="1"
              step="1"
              value={lapNumber}
              onChange={(event) =>
                setLapNumber(event.target.value.replace(/\D/g, ""))
              }
              placeholder="Fastest"
            />
          </label>
          <label>
            Data stream
            <select
              value={stream}
              onChange={(event) => {
                const next = event.target.value as
                  | "merged"
                  | "car"
                  | "position";
                setStream(next);
                if (next === "position") {
                  setChannels("X,Y,Z,Status");
                  setPlotChannel("X");
                } else {
                  setChannels("Speed,RPM,Throttle,Brake,nGear,DRS");
                  setPlotChannel("Speed");
                }
              }}
            >
              <option value="merged">Merged lap</option>
              <option value="car">Raw car data</option>
              <option value="position">Raw position data</option>
            </select>
          </label>
          <label>
            Channels
            <input
              list="telemetry-channel-presets"
              value={channels}
              onChange={(e) => setChannels(e.target.value)}
              maxLength={512}
              placeholder="Comma-separated channels"
            />
            <datalist id="telemetry-channel-presets">
              <option value="Speed,RPM,Throttle,Brake,nGear,DRS" />
              <option value="Speed,Throttle,Brake" />
              <option value="Speed,RPM,nGear" />
              <option value="X,Y,Z,Status" />
              <option value="X,Y" />
              <option value="Z,Status" />
            </datalist>
          </label>
          <label>
            Chart
            <select
              value={activePlotChannel}
              onChange={(event) => setPlotChannel(event.target.value)}
            >
              {(chartChannels.length
                ? chartChannels
                : [
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
                detail={`${lapNumber ? "Selected" : "Fastest"} lap ${t.lap}`}
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
                  value={Array.isArray(v) ? v.length : formatValue(v, k)}
                />
              ))}
          </div>
          <TrackMap
            label={sessionSummary?.location ?? sessionId}
            points={trackQuery.data?.data?.points}
            corners={trackQuery.data?.data?.corners}
            rotation={trackQuery.data?.data?.rotation}
            emptyMessage={
              trackQuery.error
                ? "The stored outline could not be loaded."
                : "Loading the stored FastF1 circuit outline."
            }
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
          columns={allDataColumns(data)}
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
                value={Array.isArray(v) ? v.length : formatValue(v, k)}
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
  const archive = useQuery({
    queryKey: ["admin-archive"],
    queryFn: () =>
      api<{
        active: boolean;
        phase: string;
        subject?: string;
        position: number;
        total: number;
        failures: number;
        timing: {
          active: boolean;
          phase: string;
          subject?: string;
          position: number;
          total: number;
          counts: Record<string, number>;
          failures: number;
          recent_sessions_per_hour: number | null;
          estimated_seconds_remaining: number | null;
          rate_sample_size: number;
        };
        coverage: {
          seasons: number;
          maps: number;
          circuits: number;
          telemetry_sessions: number;
          telemetry_laps: number;
          raw_stream_laps: number;
          outdated_telemetry_laps: number;
        };
      }>("/admin/archive"),
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
              start: 2000,
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
  const timingPosition = archive.data?.timing.position ?? 0;
  const timingTotal = archive.data?.timing.total ?? 0;
  const timingProgress = timingTotal
    ? Math.min(100, (timingPosition / timingTotal) * 100)
    : 0;
  const timingRate = archive.data?.timing.recent_sessions_per_hour;
  const timingEta = archive.data?.timing.estimated_seconds_remaining;
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
              <span>Schedules, rosters and standings from 2000</span>
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
          <section className="section">
            <div className="section-title">
              <div>
                <span>MongoDB coverage</span>
                <h2>Archive 2000–2026</h2>
              </div>
              <Status
                kind={
                  archive.data?.failures || archive.data?.timing?.failures
                    ? "warn"
                    : archive.data?.active || archive.data?.timing?.active
                      ? "live"
                      : "good"
                }
              >
                {archive.data?.active || archive.data?.timing?.active
                  ? "loading"
                  : archive.data?.phase ?? "checking"}
              </Status>
            </div>
            <div className="archive-progress">
              <div>
                <span>Modern timing scan</span>
                <strong>
                  {timingPosition.toLocaleString()} / {timingTotal.toLocaleString()} sessions
                </strong>
                <small>
                  {timingTotal ? `${timingProgress.toFixed(1)}%` : "Preparing index"}
                  {archive.data?.timing.subject
                    ? ` · ${archive.data.timing.subject}`
                    : ""}
                  {timingRate ? ` · ${timingRate.toFixed(1)} sessions/hour` : ""}
                  {timingEta != null ? ` · ${compactEta(timingEta)} remaining` : ""}
                </small>
              </div>
              <div
                className="archive-progress-track"
                role="progressbar"
                aria-label="Modern timing archive progress"
                aria-valuemin={0}
                aria-valuemax={timingTotal || 1}
                aria-valuenow={timingPosition}
              >
                <i style={{ width: `${timingProgress}%` }} />
              </div>
            </div>
            <div className="metric-grid archive-metrics">
              <Metric label="Archive phase" value={archive.data?.phase ?? "—"} />
              <Metric label="Archive item" value={archive.data?.subject ?? "—"} />
              <Metric
                label="Timing phase"
                value={archive.data?.timing?.phase ?? "—"}
              />
              <Metric
                label="Timing item"
                value={archive.data?.timing?.subject ?? "—"}
              />
              <Metric
                label="Seasons"
                value={`${archive.data?.coverage.seasons ?? 0}/27`}
              />
              <Metric
                label="Circuit maps"
                value={`${archive.data?.coverage.maps ?? 0}/${archive.data?.coverage.circuits ?? 0}`}
              />
              <Metric
                label="Telemetry sessions"
                value={archive.data?.coverage.telemetry_sessions ?? 0}
              />
              <Metric
                label="Telemetry laps"
                value={archive.data?.coverage.telemetry_laps ?? 0}
              />
              <Metric
                label="Raw stream laps"
                value={archive.data?.coverage.raw_stream_laps ?? 0}
              />
              <Metric
                label="Outdated laps"
                value={archive.data?.coverage.outdated_telemetry_laps ?? 0}
              />
              <Metric
                label="Failures"
                value={(archive.data?.failures ?? 0) + (archive.data?.timing?.failures ?? 0)}
              />
              <Metric
                label="Recent rate"
                value={timingRate ? `${timingRate.toFixed(1)}/hour` : "Calculating"}
                detail={
                  archive.data?.timing.rate_sample_size
                    ? `${archive.data.timing.rate_sample_size} verified sessions sampled`
                    : undefined
                }
              />
              <Metric
                label="Estimated remaining"
                value={compactEta(timingEta)}
                detail="Rolling estimate from verified commits"
              />
              <Metric
                label="Runners"
                value={`${archive.data?.active ? "Archive active" : "Archive stopped"} · ${archive.data?.timing?.active ? "Timing active" : "Timing stopped"}`}
              />
            </div>
          </section>
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
