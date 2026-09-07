export interface PocState {
  snapshotVersion: string;
  schemaVersion: number;
  drones: Array<Record<string, unknown>>;
  tasks: Array<Record<string, unknown>>;
  alerts: Array<Record<string, unknown>>;
  ledgers: Array<Record<string, unknown>>;
  audit: Array<Record<string, unknown>>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(`请求失败（${response.status}）`);
  return response.json() as Promise<T>;
}

export const pocApi = {
  state: () => request<PocState>("/api/state"),
  reset: () => request<{ snapshotVersion: string; auditEntry: { id: string } }>("/api/demo/reset", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmed: true })
  })
};
