import { createRouter, createWebHistory } from "vue-router";

import HomeView from "@/domains/home/views/HomeView.vue";
import SituationView from "@/domains/map/views/SituationView.vue";
import PrototypeView from "@/shared/components/PrototypeView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeView },
    { path: "/screen-overview.html", name: "situation", component: SituationView },
    { path: "/dispatch-tasks.html", name: "missions", component: PrototypeView, props: { title: "任务调度", page: "dispatch-tasks.html" } },
    { path: "/alert-workbench.html", name: "alerts", component: PrototypeView, props: { title: "告警线索处置", page: "alert-workbench.html" } },
    { path: "/stats-ledger.html", name: "ledgers", component: PrototypeView, props: { title: "巡检台账统计", page: "stats-ledger.html" } }
  ]
});
