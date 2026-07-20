const API_ROOT = import.meta.env.VITE_API_ROOT ?? "/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort(init?.signal?.reason);
  if (init?.signal?.aborted) abortFromCaller();
  else init?.signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = window.setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(`${API_ROOT}${path}`, {
      credentials: "include",
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok && response.status !== 202)
      throw new ApiError(response.status, body.detail ?? "Request failed");
    return body as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw new ApiError(504, "The local API did not respond in time. Retry the request.");
    throw error;
  } finally {
    window.clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abortFromCaller);
  }
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

export function formatValue(value: unknown, key = ""): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    if (/(^|_)(time|duration)$/i.test(key) || /Time$/.test(key))
      return duration(value);
    return value.toLocaleString(undefined, {
      maximumFractionDigits: Number.isInteger(value) ? 0 : 3,
    });
  }
  if (typeof value === "string") {
    if (
      /date|starts_at|updated_at/i.test(key) &&
      /^\d{4}-\d{2}-\d{2}T/.test(value)
    ) {
      const parsed = new Date(value);
      if (!Number.isNaN(parsed.getTime())) return localDate(value);
    }
    return value || "—";
  }
  if (Array.isArray(value)) {
    if (!value.length) return "—";
    if (value.every((item) => item == null || typeof item !== "object"))
      return value.map((item) => formatValue(item)).join(", ");
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}
