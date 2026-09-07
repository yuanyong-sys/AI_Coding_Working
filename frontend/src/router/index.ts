import { createRouter, createWebHistory } from "vue-router";

import HomeView from "@/domains/home/views/HomeView.vue";
import SituationView from "@/domains/map/views/SituationView.vue";
import PlaceholderView from "@/shared/components/PlaceholderView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeView },
    { path: "/screen-overview.html", name: "situation", component: SituationView },
    { path: "/dispatch-tasks.html", name: "missions", component: PlaceholderView, props: { title: "任务调度" } },
    { path: "/alert-workbench.html", name: "alerts", component: PlaceholderView, props: { title: "告警线索处置" } },
    { path: "/stats-ledger.html", name: "ledgers", component: PlaceholderView, props: { title: "巡检台账统计" } }
  ]
});
