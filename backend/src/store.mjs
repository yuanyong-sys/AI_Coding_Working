import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

export const SNAPSHOT_VERSION = "POC-DEMO-V1";

const standardBusinessState = Object.freeze({
  snapshotVersion: SNAPSHOT_VERSION,
  simulationClock: "2026-09-05T14:32:00+08:00",
  drones: [
    { id: "U-01", status: "ONLINE", battery: 82, taskId: "RW-20260905-001", demo: true },
    { id: "U-03", status: "BUSY", battery: 68, taskId: "RW-20260905-012", demo: true },
    { id: "U-06", status: "OFFLINE", battery: 100, taskId: null, demo: true }
  ],
  tasks: [
    { id: "RW-20260905-001", name: "惠水服务区周边巡查", status: "IN_PROGRESS", progress: 82, demo: true },
    { id: "RW-20260905-012", name: "兰海高速都匀段巡查", status: "PENDING", progress: 0, demo: true }
  ],
  alerts: [
    { id: "GJ-20260905-031", type: "交通事故", level: "EMERGENCY", status: "PENDING_VERIFICATION", demo: true },
    { id: "GJ-20260905-026", type: "烟雾火情", level: "IMPORTANT", status: "PROCESSING", demo: true }
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
