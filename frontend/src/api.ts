const API_ROOT = import.meta.env.VITE_API_ROOT ?? "/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok && response.status !== 202)
    throw new ApiError(response.status, body.detail ?? "Request failed");
  return body as T;
}

export function localDate(
  value?: string | null,
  options: Intl.DateTimeFormatOptions = {},
): string {
  if (!value) return "TBC";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    ...options,
  }).format(new Date(value));
}

export function duration(milliseconds?: number | null): string {
  if (milliseconds == null) return "—";
  const total = milliseconds / 1000;
  const minutes = Math.floor(total / 60);
  return `${minutes}:${(total % 60).toFixed(3).padStart(6, "0")}`;
}
