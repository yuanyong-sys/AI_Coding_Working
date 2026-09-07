<script setup lang="ts">
import maplibregl, { Map, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { FeatureCollection, LineString, Polygon } from "geojson";

import type { DroneSnapshot, IncursionAlert } from "@/situation";
import type { SpatialRuleVersion } from "@/spatial-rules";
import type { PlannedRoutePoint } from "@/preflight";
import { formatAnomalyType, formatClueSource, type AIClue } from "@/ai-clues";

const props = defineProps<{
  drones: DroneSnapshot[];
  spatialRules: SpatialRuleVersion[];
  leadership?: boolean;
  validationPosition?: PlannedRoutePoint;
  incursionAlert?: IncursionAlert | null;
  aiClue?: AIClue | null;
}>();
const emit = defineEmits<{ selectDrone: [drone: DroneSnapshot] }>();
const mapContainer = ref<HTMLElement>();
const viewMode = ref<"2d" | "3d">("3d");
const markers: Marker[] = [];
let map: Map | undefined;
let preflightMarker: Marker | undefined;
let incursionMarker: Marker | undefined;
let clueMarker: Marker | undefined;
const protocol = new Protocol();
const flightStateLabels: Record<string, string> = {
  pending: "待飞",
  flying: "飞行中",
  returning: "返航",
  offline: "离线",
};
const trackPointCount = computed(() =>
  props.drones.reduce((count, drone) => count + drone.track.length, 0),
);

function trackGeoJson(): FeatureCollection<LineString> {
  return {
    type: "FeatureCollection",
    features: props.drones
      .filter((drone) => drone.track.length >= 2)
      .map((drone) => ({
        type: "Feature",
        properties: {
          drone_id: drone.drone_id,
          selected: drone.drone_id === props.incursionAlert?.drone_id,
        },
        geometry: {
          type: "LineString",
          coordinates: drone.track.map((point) => [
            point.longitude,
            point.latitude,
          ]),
        },
      })),
  };
}

function renderTracks() {
  if (!map) return;
  const source = map.getSource("drone-tracks") as
    maplibregl.GeoJSONSource | undefined;
  if (source) {
    source.setData(trackGeoJson());
    return;
  }
  map.addSource("drone-tracks", { type: "geojson", data: trackGeoJson() });
  map.addLayer({
    id: "drone-tracks-glow",
    type: "line",
    source: "drone-tracks",
    paint: {
      "line-color": "#55e2d5",
      "line-width": 7,
      "line-opacity": 0.13,
    },
  });
  map.addLayer({
    id: "drone-tracks",
    type: "line",
    source: "drone-tracks",
    paint: {
      "line-color": ["case", ["get", "selected"], "#ff5967", "#70fff0"],
      "line-width": ["case", ["get", "selected"], 4, 2],
      "line-opacity": 0.9,
      "line-dasharray": [2, 2],
    },
  });
}

function renderMarkers() {
  if (!map) return;
  for (const marker of markers.splice(0)) marker.remove();
  for (const drone of props.drones) {
    const element = document.createElement(props.leadership ? "button" : "div");
    const flightStateLabel =
      flightStateLabels[drone.flight_state] ?? drone.flight_state;
    element.className = `drone-marker drone-marker--${drone.source_type}`;
    if (drone.drone_id === props.incursionAlert?.drone_id) {
      element.classList.add("drone-marker--alert");
    }
    element.dataset.testid = `drone-marker-${drone.drone_id}`;
    element.dataset.position = `${drone.longitude},${drone.latitude}`;
    element.dataset.flightState = drone.flight_state;
    if (props.leadership) {
      (element as HTMLButtonElement).type = "button";
      element.setAttribute(
        "aria-label",
        `查看巡检无人机 ${drone.drone_id} 设备信息`,
      );
      element.addEventListener("click", () => emit("selectDrone", drone));
    } else {
      element.setAttribute("role", "img");
      element.setAttribute(
        "aria-label",
        `${drone.drone_id}，${flightStateLabel}，高度 ${drone.altitude_m} 米，${drone.source_type === "simulated" ? "模拟数据" : "真实数据"}`,
      );
    }
    const pulse = document.createElement("span");
    pulse.className = "drone-marker__pulse";
    const body = document.createElement("span");
    body.className = "drone-marker__body";
    body.textContent = "◆";
    const label = document.createElement("strong");
    label.textContent = drone.drone_id;
    const state = document.createElement("em");
    state.textContent = flightStateLabel;
    label.append(state);
    element.append(pulse, body, label);
    markers.push(
      new maplibregl.Marker({ element, anchor: "center" })
        .setLngLat([drone.longitude, drone.latitude])
        .addTo(map),
    );
  }
}

const buildingSites = [
  [106.613, 26.658, 0.0022, 0.0013, 120],
  [106.617, 26.655, 0.0018, 0.0015, 86],
  [106.622, 26.653, 0.0025, 0.0012, 155],
  [106.631, 26.651, 0.002, 0.0016, 110],
  [106.637, 26.648, 0.0024, 0.0014, 178],
  [106.643, 26.644, 0.0017, 0.0012, 94],
  [106.648, 26.641, 0.0026, 0.0015, 138],
] as const;

function renderLeadershipBuildings() {
  if (!map || !props.leadership) return;
  const buildings: FeatureCollection<Polygon> = {
    type: "FeatureCollection",
    features: buildingSites.map(([lon, lat, width, depth, height]) => ({
      type: "Feature",
      properties: { height },
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [lon - width, lat - depth],
            [lon + width, lat - depth],
            [lon + width, lat + depth],
            [lon - width, lat + depth],
            [lon - width, lat - depth],
          ],
        ],
      },
    })),
  };
  map.addSource("leadership-buildings", { type: "geojson", data: buildings });
  map.addLayer({
    id: "leadership-buildings",
    type: "fill-extrusion",
    source: "leadership-buildings",
    paint: {
      "fill-extrusion-color": [
        "interpolate",
        ["linear"],
        ["get", "height"],
        80,
        "#17464d",
        180,
        "#55c9c8",
      ],
      "fill-extrusion-height": ["get", "height"],
      "fill-extrusion-base": 0,
      "fill-extrusion-opacity": 0.78,
    },
  });
}

function toggleViewMode() {
  if (!map || !props.leadership) return;
  viewMode.value = viewMode.value === "3d" ? "2d" : "3d";
  const threeDimensional = viewMode.value === "3d";
  map.easeTo({
    pitch: threeDimensional ? 52 : 0,
    bearing: threeDimensional ? -13 : 0,
    duration: 500,
  });
  if (map.getLayer("leadership-buildings")) {
    map.setPaintProperty(
      "leadership-buildings",
      "fill-extrusion-height",
      threeDimensional ? ["get", "height"] : 0,
    );
  }
}

function syncCameraState() {
  if (!map || !mapContainer.value || !props.leadership) return;
  const center = map.getCenter();
  mapContainer.value.dataset.camera = [
    center.lng.toFixed(5),
    center.lat.toFixed(5),
    map.getZoom().toFixed(2),
    map.getPitch().toFixed(1),
    map.getBearing().toFixed(1),
  ].join(",");
  const threeDimensional = map.getPitch() > 1;
  viewMode.value = threeDimensional ? "3d" : "2d";
  if (map.getLayer("leadership-buildings")) {
    map.setPaintProperty(
      "leadership-buildings",
      "fill-extrusion-height",
      threeDimensional ? ["get", "height"] : 0,
    );
  }
}

function spatialRulesGeoJson(): FeatureCollection<Polygon> {
  return {
    type: "FeatureCollection",
    features: props.spatialRules.map((rule) => ({
      type: "Feature",
      properties: {
        rule_id: rule.rule_id,
        rule_type: rule.rule_type,
        name: rule.name,
        selected: rule.rule_id === props.incursionAlert?.rule_id,
      },
      geometry: rule.geometry,
    })),
  };
}

function renderSpatialRules() {
  if (!map) return;
  const source = map.getSource("spatial-rules") as
    maplibregl.GeoJSONSource | undefined;
  if (source) {
    source.setData(spatialRulesGeoJson());
    return;
  }
  map.addSource("spatial-rules", {
    type: "geojson",
    data: spatialRulesGeoJson(),
  });
  map.addLayer({
    id: "spatial-rules-fill",
    type: "fill",
    source: "spatial-rules",
    paint: {
      "fill-color": [
        "match",
        ["get", "rule_type"],
        "no_fly_zone",
        "#ff5967",
        "#55e2d5",
      ],
      "fill-opacity": ["case", ["get", "selected"], 0.42, 0.2],
    },
  });
  map.addLayer({
    id: "spatial-rules-outline",
    type: "line",
    source: "spatial-rules",
    paint: {
      "line-color": [
        "match",
        ["get", "rule_type"],
        "no_fly_zone",
        "#ff5967",
        "#55e2d5",
      ],
      "line-width": 2,
      "line-dasharray": [2, 1],
    },
  });
}

function renderIncursionAlert() {
  incursionMarker?.remove();
  incursionMarker = undefined;
  if (!map) return;
  renderSpatialRules();
  renderTracks();
  renderMarkers();
  if (!props.incursionAlert) return;
  const element = document.createElement("div");
  element.className = "incursion-alert-marker";
  element.dataset.testid = "incursion-alert-position";
  element.setAttribute("role", "img");
  element.setAttribute(
    "aria-label",
    `${props.incursionAlert.drone_id} 越界告警命中位置`,
  );
  element.textContent = "!";
  incursionMarker = new maplibregl.Marker({ element, anchor: "center" })
    .setLngLat([props.incursionAlert.longitude, props.incursionAlert.latitude])
    .addTo(map);
  map.flyTo({
    center: [props.incursionAlert.longitude, props.incursionAlert.latitude],
    zoom: Math.max(map.getZoom(), 14),
  });
}

function renderPreflightPosition() {
  preflightMarker?.remove();
  preflightMarker = undefined;
  if (!map || !props.validationPosition) return;
  const element = document.createElement("div");
  element.className = "preflight-result-marker";
  element.dataset.testid = "preflight-result-position";
  element.dataset.position = `${props.validationPosition.longitude},${props.validationPosition.latitude}`;
  element.setAttribute("role", "img");
  element.setAttribute("aria-label", "航前规则校验命中位置");
  element.textContent = "×";
  preflightMarker = new maplibregl.Marker({ element, anchor: "center" })
    .setLngLat([
      props.validationPosition.longitude,
      props.validationPosition.latitude,
    ])
    .addTo(map);
  map.flyTo({
    center: [
      props.validationPosition.longitude,
      props.validationPosition.latitude,
    ],
    zoom: Math.max(map.getZoom(), 14),
  });
}

function renderAIClue() {
  clueMarker?.remove();
  clueMarker = undefined;
  if (!map || !props.aiClue) return;
  const element = document.createElement("div");
  element.className = "ai-clue-marker";
  element.dataset.testid = "ai-clue-position";
  element.dataset.position = `${props.aiClue.location.longitude},${props.aiClue.location.latitude}`;
  element.setAttribute("role", "img");
  const clueType = formatAnomalyType(props.aiClue.anomaly_type);
  const confidence = `${(props.aiClue.confidence * 100).toFixed(1)}%`;
  element.setAttribute(
    "aria-label",
    `${clueType}，置信度 ${confidence}，${formatClueSource(props.aiClue.source_type)}，来源时间 ${props.aiClue.source_time}`,
  );
  const glyph = document.createElement("span");
  glyph.textContent = "◎";
  const label = document.createElement("strong");
  label.textContent = `${clueType} · ${confidence}`;
  const meta = document.createElement("small");
  meta.textContent = formatClueSource(props.aiClue.source_type);
  element.append(glyph, label, meta);
  clueMarker = new maplibregl.Marker({ element, anchor: "center" })
    .setLngLat([
      props.aiClue.location.longitude,
      props.aiClue.location.latitude,
    ])
    .addTo(map);
  map.flyTo({
    center: [props.aiClue.location.longitude, props.aiClue.location.latitude],
    zoom: Math.max(map.getZoom(), 14),
  });
}

function renderSituation() {
  renderSpatialRules();
  renderTracks();
  renderMarkers();
  renderPreflightPosition();
  renderIncursionAlert();
  renderAIClue();
}

onMounted(() => {
  if (!mapContainer.value) return;
  maplibregl.addProtocol("pmtiles", protocol.tile);
  map = new maplibregl.Map({
    container: mapContainer.value,
    center: [106.6282, 26.6467],
    zoom: props.leadership ? 12.8 : 12.2,
    pitch: props.leadership ? 52 : 0,
    bearing: props.leadership ? -13 : 0,
    maxPitch: 75,
    attributionControl: false,
    style: {
      version: 8,
      sources: {
        guanshanhu: {
          type: "raster",
          url: "pmtiles:///maps/guanshanhu.pmtiles",
          tileSize: 256,
          attribution: "POC 演示底图",
        },
      },
      layers: [
        {
          id: "guanshanhu-basemap",
          type: "raster",
          source: "guanshanhu",
          paint: { "raster-opacity": 0.92 },
        },
      ],
    },
  });
  map.addControl(
    new maplibregl.NavigationControl({ showCompass: true }),
    "bottom-right",
  );
  map.on("load", () => {
    renderLeadershipBuildings();
    renderSituation();
    syncCameraState();
  });
  map.on("move", syncCameraState);
});

watch(
  () => props.drones,
  () => {
    renderTracks();
    renderMarkers();
  },
  { deep: true },
);
watch(() => props.spatialRules, renderSpatialRules, { deep: true });
watch(() => props.validationPosition, renderPreflightPosition, { deep: true });
watch(() => props.incursionAlert, renderIncursionAlert, { deep: true });
watch(() => props.aiClue, renderAIClue, { deep: true });

onBeforeUnmount(() => {
  for (const marker of markers.splice(0)) marker.remove();
  preflightMarker?.remove();
  incursionMarker?.remove();
  clueMarker?.remove();
  map?.remove();
  maplibregl.removeProtocol("pmtiles");
});
</script>

<template>
  <section
    ref="mapContainer"
    class="situation-map"
    :data-testid="leadership ? 'leadership-map' : 'situation-map'"
    :data-view-mode="leadership ? viewMode : undefined"
    data-center="106.6282,26.6467"
    :data-track-points="trackPointCount"
    :data-spatial-rule-count="spatialRules.length"
    :aria-label="
      leadership
        ? '贵阳市观山湖区三维巡查态势地图'
        : '贵阳市观山湖区低空运行地图'
    "
  >
    <button
      v-if="leadership"
      type="button"
      class="map-view-toggle"
      :aria-label="viewMode === '3d' ? '切换为二维视图' : '切换为三维视图'"
      :aria-pressed="viewMode === '3d'"
      @click="toggleViewMode"
    >
      {{ viewMode === "3d" ? "三维视图" : "二维视图" }}
    </button>
    <div v-if="!leadership" class="map-coordinate" aria-hidden="true">
      观山湖区 · 106.6282°E / 26.6467°N
    </div>
    <div v-if="!leadership" class="track-summary">
      航迹 · {{ trackPointCount }} 个遥测点
    </div>
    <div v-if="spatialRules.length" class="spatial-rule-map-labels">
      <article
        v-for="rule in spatialRules"
        :key="`${rule.rule_id}-${rule.version}`"
        :data-rule-type="rule.rule_type"
      >
        <strong>
          {{ rule.rule_type === "no_fly_zone" ? "禁飞区" : "电子围栏" }} ·
          {{ rule.name }}
        </strong>
        <span>版本 v{{ rule.version }} · {{ rule.coordinate_reference }}</span>
        <span>来源 · {{ rule.source }}</span>
      </article>
    </div>
  </section>
</template>
