import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

export const SNAPSHOT_VERSION = "POC-DEMO-V1";
const SCHEMA_VERSION = 2;

const standardBusinessState = Object.freeze({
  snapshotVersion: SNAPSHOT_VERSION,
  schemaVersion: SCHEMA_VERSION,
  simulationClock: "2026-09-05T14:32:00+08:00",
  drones: [
    { id: "U-01", status: "ONLINE", battery: 82, taskId: "RW-20260905-001", task: "贵北高速惠水段航线巡检", x: 300, y: 300, demo: true },
    { id: "U-02", status: "ONLINE", battery: 76, taskId: "RW-20260905-002", task: "惠水服务区周边巡查", x: 500, y: 180, demo: true },
    { id: "U-03", status: "BUSY", battery: 45, taskId: "RW-20260905-012", task: "福泉马场坪段夜间巡查", x: 700, y: 700, demo: true },
    { id: "U-04", status: "ALARM", battery: 61, taskId: "RW-20260905-004", task: "交通事故现场跟拍", x: 470, y: 420, demo: true },
    { id: "U-05", status: "ONLINE", battery: 88, taskId: "RW-20260905-005", task: "独山段秩序巡查", x: 650, y: 520, demo: true },
    { id: "U-06", status: "OFFLINE", battery: 100, taskId: null, task: "机库待命", x: 150, y: 800, demo: true },
    { id: "U-07", status: "ONLINE", battery: 69, taskId: "RW-20260905-007", task: "贵定段连接线隐患排查", x: 820, y: 330, demo: true },
    { id: "U-08", status: "BUSY", battery: 34, taskId: "RW-20260905-008", task: "三都荔波段秩序巡查", x: 240, y: 560, demo: true }
  ],
  tasks: [
    { id: "RW-20260905-002", name: "惠水服务区周边巡查", status: "IN_PROGRESS", progress: 82, overdue: false, demo: true },
    { id: "RW-20260905-001", name: "贵北高速惠水段航线巡检", status: "IN_PROGRESS", progress: 68, overdue: false, demo: true },
    { id: "RW-20260905-008", name: "三都荔波段秩序巡查", status: "IN_PROGRESS", progress: 57, overdue: false, demo: true },
    { id: "RW-20260905-012", name: "福泉马场坪段夜间巡查", status: "PENDING", progress: 45, overdue: true, demo: true },
    { id: "RW-20260905-007", name: "贵定段连接线隐患排查", status: "PENDING", progress: 31, overdue: true, demo: true }
  ],
  alerts: [
    { id: "GJ-20260905-031", type: "交通事故", level: "EMERGENCY", status: "PENDING_VERIFICATION", location: "兰海高速 K1582 都匀段", time: "14:32", x: 470, y: 420, demo: true },
    { id: "GJ-20260905-026", type: "烟雾火情", level: "IMPORTANT", status: "PROCESSING", location: "荔波互通匝道", time: "14:18", x: 720, y: 690, demo: true },
    { id: "GJ-20260905-024", type: "车辆违停", level: "IMPORTANT", status: "PROCESSING", location: "贵定连接线应急车道", time: "13:56", x: 210, y: 540, demo: true },
    { id: "GJ-20260905-019", type: "行人闯入", level: "INFO", status: "PENDING_VERIFICATION", location: "厦蓉高速 K1380 桩号", time: "13:40", x: 830, y: 320, demo: true },
    { id: "GJ-20260905-017", type: "道路积水", level: "INFO", status: "PENDING_VERIFICATION", location: "沪昆高速交汇桥下", time: "12:52", x: 660, y: 530, demo: true },
    { id: "GJ-20260905-014", type: "车辆违停", level: "IMPORTANT", status: "RESOLVED", location: "福泉服务区入口", time: "11:47", x: 640, y: 250, demo: true },
    { id: "GJ-20260905-008", type: "行人聚集", level: "INFO", status: "RESOLVED", location: "惠水服务区停车区", time: "08:15", x: 500, y: 150, demo: true }
  ],
  ledgers: [
    { id: "TZ-20260905-001", taskId: "RW-20260905-001", mileageKm: 38.6, clueCount: 2, demo: true }
  ]
});

function clone(value) {
  return structuredClone(value);
}

function initialDocument() {
  return { ...clone(standardBusinessState), audit: [] };
}

export class PocStore {
  constructor(databasePath) {
    this.databasePath = databasePath;
    this.document = null;
    this.writeQueue = Promise.resolve();
  }

  async initialize() {
    if (this.document) return;
    try {
      this.document = JSON.parse(await readFile(this.databasePath, "utf8"));
      if (this.document.schemaVersion !== SCHEMA_VERSION) {
        this.document = { ...clone(standardBusinessState), audit: this.document.audit ?? [] };
        await this.persist();
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      this.document = initialDocument();
      await this.persist();
    }
  }

  async read() {
    await this.initialize();
    return clone(this.document);
  }

  async updateEntity(collection, id, changes) {
    await this.initialize();
    const entity = this.document[collection]?.find((item) => item.id === id);
    if (!entity) return null;
    Object.assign(entity, changes);
    await this.persist();
    return clone(entity);
  }

  async reset() {
    await this.initialize();
    const audit = this.document.audit;
    this.document = {
      ...clone(standardBusinessState),
      audit: [
        ...audit,
        {
          id: `AUDIT-${String(audit.length + 1).padStart(4, "0")}`,
          action: "DEMO_RESET",
          snapshotVersion: SNAPSHOT_VERSION,
          occurredAt: new Date().toISOString()
        }
      ]
    };
    await this.persist();
    return this.read();
  }

  async persist() {
    const serialized = JSON.stringify(this.document, null, 2);
    this.writeQueue = this.writeQueue.then(async () => {
      await mkdir(path.dirname(this.databasePath), { recursive: true });
      const temporaryPath = `${this.databasePath}.tmp`;
      await writeFile(temporaryPath, serialized, "utf8");
      await rename(temporaryPath, this.databasePath);
    });
    await this.writeQueue;
  }
}

export function businessState(document) {
  const { audit: _audit, ...business } = document;
  return business;
}
