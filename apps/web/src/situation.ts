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
  data_status: "current" | "delayed" | "offline";
  track: TrackPoint[];
}

export interface TrackPoint {
  event_id: string;
  longitude: number;
  latitude: number;
  altitude_m: number;
  source_time: string;
}

export interface SituationSnapshot {
  drones: DroneSnapshot[];
  metrics: SituationMetrics;
  cursor: number;
}

export interface SituationMetrics {
  flight_sorties: number;
  online_rate: number;
  in_flight_count: number;
  telemetry_delay_seconds: number;
  source_composition: { real: number; simulated: number };
  observation_window_seconds: number;
}

export interface SituationStreamMessage {
  type: "telemetry" | "snapshot_required";
  cursor: number;
}

export function connectSituationEvents(
  after: number,
  onMessage: (message: SituationStreamMessage) => void,
  onDisconnect: () => void,
): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(
    `${protocol}//${window.location.host}/api/situation/events?after=${after}`,
  );
  socket.addEventListener("message", (event) => {
    onMessage(JSON.parse(event.data as string) as SituationStreamMessage);
  });
  socket.addEventListener("close", onDisconnect);
  return socket;
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
