<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import SituationMap from "@/components/SituationMap.vue";
import {
  fetchSituationSnapshot,
  formatSourceTime,
  type DroneSnapshot,
} from "@/situation";

const drones = ref<DroneSnapshot[]>([]);
const loading = ref(true);
const error = ref("");
const selectedDrone = computed(() => drones.value[0]);

onMounted(async () => {
  try {
    drones.value = (await fetchSituationSnapshot()).drones;
  } catch (reason) {
    error.value =
      reason instanceof Error ? reason.message : "态势快照暂时不可用";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <main class="command-shell">
    <header class="command-header">
      <div class="brand-block">
        <span class="brand-index">LOW ALTITUDE / GSH-01</span>
        <h1>低空态势总览</h1>
      </div>
      <div class="system-state" role="status">
        <span class="live-signal" aria-hidden="true"></span>
        本地态势链路
        <strong>{{ error ? "异常" : "正常" }}</strong>
      </div>
      <div class="header-place">
        <span>贵阳市</span>
        <strong>观山湖区</strong>
      </div>
    </header>

    <aside class="overview-rail" aria-label="运行摘要">
      <p class="section-code">01 / CURRENT PICTURE</p>
      <h2>当前图景</h2>
      <div class="metric">
        <span>已接入无人机</span>
        <strong>{{ drones.length.toString().padStart(2, "0") }}</strong>
      </div>
      <div class="metric metric--accent">
        <span>当前在飞</span>
        <strong>{{
          drones
            .filter((drone) => drone.flight_state === "flying")
            .length.toString()
            .padStart(2, "0")
        }}</strong>
      </div>
      <div v-if="selectedDrone" class="source-card">
        <span class="source-badge">模拟数据</span>
        <p>来源时间</p>
        <time :datetime="selectedDrone.source_time">
          {{ formatSourceTime(selectedDrone.source_time) }}
        </time>
        <dl>
          <div>
            <dt>高度</dt>
            <dd>{{ selectedDrone.altitude_m }} m</dd>
          </div>
          <div>
            <dt>速度</dt>
            <dd>{{ selectedDrone.speed_mps }} m/s</dd>
          </div>
          <div>
            <dt>航向</dt>
            <dd>{{ selectedDrone.heading_deg }}°</dd>
          </div>
        </dl>
      </div>
      <p v-else-if="loading" class="quiet-state" role="status">
        正在读取态势快照…
      </p>
      <p v-else-if="error" class="error-state" role="alert">{{ error }}</p>
      <p v-else class="quiet-state" role="status">暂无已接入无人机</p>
    </aside>

    <section class="map-stage">
      <SituationMap :drones="drones" />
      <div class="map-legend" aria-label="地图图例">
        <span><i class="legend-dot"></i> 已接入无人机</span>
        <span><i class="legend-ring"></i> 模拟来源</span>
      </div>
    </section>

    <aside class="signal-rail" aria-label="实时信号">
      <p class="section-code">SIGNAL / LIVE</p>
      <h2>实时信号</h2>
      <article v-if="selectedDrone" class="signal-item">
        <time :datetime="selectedDrone.source_time">
          {{ formatSourceTime(selectedDrone.source_time).slice(11) }}
        </time>
        <strong>遥测接入成功</strong>
        <p>观山湖中心区 · {{ selectedDrone.altitude_m }} 米</p>
        <span>SIM / {{ selectedDrone.flight_state.toUpperCase() }}</span>
      </article>
      <p v-else class="quiet-state">等待首个遥测信号</p>
      <div class="scope-note">
        <span>MAP PACKAGE</span>
        <strong>GSH · LOCAL · V1</strong>
        <p>底图由浏览器直接读取本地 PMTiles，无独立瓦片进程。</p>
      </div>
    </aside>

    <footer class="command-footer">
      <span>PRIVATE / OFFLINE CAPABLE</span>
      <div class="activity-line">
        <i></i><i></i><i></i><i></i><i></i><i></i>
      </div>
      <strong>态势快照 · HTTP</strong>
    </footer>
  </main>
</template>
