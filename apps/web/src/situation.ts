export interface DroneSnapshot {
  drone_id: string;
  longitude: number;
  latitude: number;
  altitude_m: number;
  heading_deg: number;
  speed_mps: number;
  flight_state: string;
  source_time: string;
  platform_received_time: string;
  source_type: "simulated" | "real";
}

export interface SituationSnapshot {
  drones: DroneSnapshot[];
}

export async function fetchSituationSnapshot(): Promise<SituationSnapshot> {
  const response = await fetch("/api/situation/snapshot");
  if (!response.ok) throw new Error("态势快照暂时不可用");
  return response.json() as Promise<SituationSnapshot>;
}

export function formatSourceTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
    .format(new Date(value))
    .replaceAll("/", "-");
}
