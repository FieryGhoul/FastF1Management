export type ApiEnvelope<T> = {
  data: T;
  source?: string;
  updated_at?: string;
  availability?: string;
  unavailable_reason?: string | null;
  job_id?: string | null;
  status?: string | null;
};

export type SessionRef = {
  id: string;
  name: string;
  code: string;
  starts_at: string | null;
};
export type RaceEvent = {
  id: string;
  season: number;
  round: number;
  name: string;
  official_name?: string;
  country: string;
  location: string;
  circuit_slug?: string;
  event_date: string | null;
  format?: string;
  f1_api_support: boolean;
  sessions: SessionRef[];
};
export type Circuit = {
  slug: string;
  external_id?: string;
  name: string;
  country: string;
  locality?: string;
  latitude?: number;
  longitude?: number;
  length_km?: number;
  race_laps?: number;
  lap_record?: string;
  first_grand_prix?: number;
  circuit_type?: string;
  corner_count?: number;
  direction?: string;
  source_url?: string;
  event_count?: number;
  session_count?: number;
  events?: RaceEvent[];
  map_data?: {
    points?: TrackPoint[];
    corners?: TrackPoint[];
    marshal_lights?: TrackPoint[];
    marshal_sectors?: TrackPoint[];
    rotation?: number;
  };
};
export type TrackPoint = {
  X: number;
  Y: number;
  Distance?: number;
  Speed?: number;
  Number?: number;
  Letter?: string;
};
export type Job = {
  job_id?: string;
  id?: string;
  status: string;
  progress: number;
  error?: string;
  artifact_key?: string;
};
export type LiveState = {
  state: string;
  honest_live: boolean;
  message: string;
  event: RaceEvent | null;
  session: SessionRef | null;
  recent_session: SessionRef | null;
  checked_at: string;
};
