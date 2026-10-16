const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function detailOf(response: Response): Promise<unknown> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return body.detail ?? `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response));
  }
  return response.json() as Promise<T>;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response));
  }
  return response.json() as Promise<T>;
}

/** Flatten a FastAPI `detail` (string | list | field map) into messages. */
export function errorMessages(error: unknown): string[] {
  if (error instanceof ApiError) {
    const d = error.detail;
    if (typeof d === 'string') return [d];
    if (Array.isArray(d)) return d.map(String);
    if (d && typeof d === 'object') return Object.values(d).map(String);
  }
  return [error instanceof Error ? error.message : String(error)];
}
