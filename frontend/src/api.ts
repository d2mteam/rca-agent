export interface Project {
  name: string;
  compose_path?: string;
}
export interface Check {
  name: string;
  status: "pass" | "fail" | "skip";
  expected: unknown;
  actual: unknown;
  detail?: string;
}
export interface Verification {
  checks: Check[];
  passed: boolean;
}
export interface PlanEvent {
  id?: string | number;
  kind: string;
  message: string;
  data?: unknown;
  created_at?: string;
}
export interface Plan {
  id: string;
  project: string;
  status: string;
  plan_hash: string;
  request: string;
  planner: string;
  created_at: string;
  compose_yaml: string;
  input_yaml?: string;
  previous_yaml: string;
  infrastructure_spec?: Record<string, unknown>;
  diff: string;
  changes: { service: string; action: string; detail: string }[];
  checks: Record<string, unknown>[];
  verification?: Verification;
  error?: string;
  warnings?: string[];
  events?: PlanEvent[];
}
export interface ProjectState {
  name: string;
  compose_yaml: string;
  runtime?: unknown;
  verified_state?: unknown;
}
export interface Health {
  status: string;
  docker: unknown;
  planner: unknown;
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail ?? data?.error ?? `HTTP ${response.status}`;
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return data as T;
}

export const isRunning = (status?: string) =>
  status === "applying" ||
  status === "applied" ||
  status === "verifying" ||
  status === "approved" ||
  status === "executing";
export const displayValue = (value: unknown) =>
  value == null
    ? "—"
    : typeof value === "string"
      ? value
      : JSON.stringify(value, null, 2);
export function dockerReady(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string")
    return ["ok", "available", "connected", "ready", "running"].includes(
      value.toLowerCase(),
    );
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return (
      record.available === true ||
      record.connected === true ||
      record.ok === true ||
      record.status === "ok" ||
      record.status === "ready"
    );
  }
  return false;
}
