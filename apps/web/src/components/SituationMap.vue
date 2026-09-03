<script setup lang="ts">
import maplibregl, { Map, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { FeatureCollection, LineString } from "geojson";

import type { DroneSnapshot } from "@/situation";

const props = defineProps<{ drones: DroneSnapshot[] }>();
const mapContainer = ref<HTMLElement>();
const markers: Marker[] = [];
let map: Map | undefined;
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
        properties: { drone_id: drone.drone_id },
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
      "line-color": "#70fff0",
      "line-width": 2,
      "line-opacity": 0.9,
      "line-dasharray": [2, 2],
    },
  });
}

function renderMarkers() {
  if (!map) return;
  for (const marker of markers.splice(0)) marker.remove();
  for (const drone of props.drones) {
    const element = document.createElement("div");
    const flightStateLabel =
      flightStateLabels[drone.flight_state] ?? drone.flight_state;
    element.className = `drone-marker drone-marker--${drone.source_type}`;
    element.dataset.testid = `drone-marker-${drone.drone_id}`;
    element.dataset.position = `${drone.longitude},${drone.latitude}`;
    element.dataset.flightState = drone.flight_state;
    element.setAttribute("role", "img");
    element.setAttribute(
      "aria-label",
      `${drone.drone_id}，${flightStateLabel}，高度 ${drone.altitude_m} 米，${drone.source_type === "simulated" ? "模拟数据" : "真实数据"}`,
    );
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

function renderSituation() {
  renderTracks();
  renderMarkers();
}

onMounted(() => {
  if (!mapContainer.value) return;
  maplibregl.addProtocol("pmtiles", protocol.tile);
  map = new maplibregl.Map({
    container: mapContainer.value,
    center: [106.6282, 26.6467],
    zoom: 12.2,
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
  map.on("load", renderSituation);
});

watch(() => props.drones, renderSituation, { deep: true });

onBeforeUnmount(() => {
  for (const marker of markers.splice(0)) marker.remove();
  map?.remove();
  maplibregl.removeProtocol("pmtiles");
});
</script>

<template>
  <section
    ref="mapContainer"
    class="situation-map"
    data-testid="situation-map"
    data-center="106.6282,26.6467"
    :data-track-points="trackPointCount"
    aria-label="贵阳市观山湖区低空运行地图"
  >
    <div class="map-coordinate" aria-hidden="true">
      观山湖区 · 106.6282°E / 26.6467°N
    </div>
    <div class="track-summary">航迹 · {{ trackPointCount }} 个遥测点</div>
  </section>
</template>
