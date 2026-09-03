<script setup lang="ts">
import maplibregl, { Map, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { DroneSnapshot } from "@/situation";

const props = defineProps<{ drones: DroneSnapshot[] }>();
const mapContainer = ref<HTMLElement>();
const markers: Marker[] = [];
let map: Map | undefined;
const protocol = new Protocol();

function renderMarkers() {
  if (!map) return;
  for (const marker of markers.splice(0)) marker.remove();
  for (const drone of props.drones) {
    const element = document.createElement("div");
    element.className = `drone-marker drone-marker--${drone.source_type}`;
    element.dataset.testid = `drone-marker-${drone.drone_id}`;
    element.setAttribute("role", "img");
    element.setAttribute(
      "aria-label",
      `${drone.drone_id}，高度 ${drone.altitude_m} 米，${drone.source_type === "simulated" ? "模拟数据" : "真实数据"}`,
    );
    element.innerHTML = `<span class="drone-marker__pulse"></span><span class="drone-marker__body">◆</span><strong>${drone.drone_id}</strong>`;
    markers.push(
      new maplibregl.Marker({ element, anchor: "center" })
        .setLngLat([drone.longitude, drone.latitude])
        .addTo(map),
    );
  }
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
  map.on("load", renderMarkers);
});

watch(() => props.drones, renderMarkers, { deep: true });

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
    aria-label="贵阳市观山湖区低空运行地图"
  >
    <div class="map-coordinate" aria-hidden="true">
      观山湖区 · 106.6282°E / 26.6467°N
    </div>
  </section>
</template>
