<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import * as echarts from "echarts";
import { SmoothieChart, TimeSeries } from "smoothie";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity } from "lucide-vue-next";
import { apiRequest, formatApiError } from "@/lib/api";
import { useTranslation } from "@/lib/i18n";
import { bytesToHuman, extractArray } from "@/lib/device-detail-data";

type DeviceStatusPoint = {
  timestamp?: number;
  cpu_percent?: number;
  cpu_times_idle?: number;
  mem_percent?: number;
  cpu_times_user?: number;
  cpu_times_system?: number;
  core_temperature?: number;
  mem_shared?: number;
  mem_free?: number;
  mem_buffers?: number;
  mem_cached?: number;
  mem_available?: number;
  fd_count?: number;
  tcpcon_count?: number;
  udpcon_count?: number;
  thread_count?: number;
  crash_count?: number;
  net_io_bytes_sent?: number;
  net_io_bytes_recv?: number;
};

const props = defineProps<{ deviceId: string; liveStatus?: DeviceStatusPoint; sampledAt?: number }>();
const { t } = useTranslation();
const loading = ref(true);
const loadError = ref("");
const container = ref<HTMLElement>();
const historyRequest = new AbortController();
let resizeObserver: ResizeObserver | undefined;
const C = {
  cpu: "#00ff00",
  cpuUser: "#f39c12",
  cpuSystem: "#2e86c1",
  cpuTemp: "#e11d48",
  netUp: "#cb4d1c",
  netDown: "#70bf56",
  file: "#f97316",
  socketTcp: "#8b5cf6",
  socketUdp: "#ec4899",
  thread: "#6366f1",
  crash: "#ef4444",
  free: "#3498db",
  buffer: "#f1c40f",
  cached: "#3a4899",
  available: "#10b981",
};

function statusTimestampToMs(value?: number) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n > 1e11 ? Math.floor(n) : Math.floor(n * 1000);
}

function bytesToMB(bytes?: number) {
  return Number((Number(bytes || 0) / (1024 * 1024)).toFixed(2));
}

const historyPoints = shallowRef<DeviceStatusPoint[]>([]);

const realtimeUsage = ref({ cpu: 0, mem: 0 });
const latestRate = ref({ up: 0, down: 0 });
const isDarkMode = ref(
  typeof document !== "undefined" && document.documentElement.classList.contains("dark"),
);

const rtCpuCanvasRef = ref<HTMLCanvasElement | null>(null);
const rtMemCanvasRef = ref<HTMLCanvasElement | null>(null);
const rtNetCanvasRef = ref<HTMLCanvasElement | null>(null);
const cpuChartRef = ref<HTMLDivElement | null>(null);
const resChartRef = ref<HTMLDivElement | null>(null);
const memChartRef = ref<HTMLDivElement | null>(null);

let wsInstance: WebSocket | null = null;
let wsTimer: number | null = null;

const cpuPercentRef = ref(0);
const memPercentRef = ref(0);
const netUpRef = ref(0);
const netDownRef = ref(0);

const prevNetCounterRef = ref<{ sent: number; recv: number; tsMs: number } | null>(null);

const realtimeChartsRef = shallowRef<{ cpu?: SmoothieChart; mem?: SmoothieChart; net?: SmoothieChart }>({});
const realtimeSeriesRef = shallowRef<{
  cpu?: TimeSeries;
  cpuIdle?: TimeSeries;
  cpuUser?: TimeSeries;
  cpuSystem?: TimeSeries;
  mem?: TimeSeries;
  memBuffers?: TimeSeries;
  memCached?: TimeSeries;
  memShared?: TimeSeries;
  netUp?: TimeSeries;
  netDown?: TimeSeries;
}>({});
const staticChartsRef = shallowRef<{ cpu?: echarts.ECharts; resource?: echarts.ECharts; memory?: echarts.ECharts }>({});
const hasTrendData = computed(() => historyPoints.value.length > 0);

let themeObserver: MutationObserver | null = null;

const setupRealtimeCharts = () => {
  if (!rtCpuCanvasRef.value || !rtMemCanvasRef.value || !rtNetCanvasRef.value) return;
  const rtGridFill = isDarkMode.value ? "rgba(15, 23, 42, 0.55)" : "white";
  const rtGridStroke = isDarkMode.value ? "rgba(148, 163, 184, 0.18)" : "#f5f5f5";
  const rtLabelColor = isDarkMode.value ? "rgba(226, 232, 240, 0.8)" : "black";
  const rtTipColor = isDarkMode.value ? "rgba(203, 213, 225, 0.35)" : "#bbbbbb";

  const memChart = new SmoothieChart({
    responsive: true,
    yMinFormatter: () => "",
    yMaxFormatter: () => `${memPercentRef.value.toFixed(1)}% MEM`,
    millisPerPixel: 100,
    grid: { lineWidth: 1, millisPerLine: 500, verticalSections: 1, fillStyle: rtGridFill, strokeStyle: rtGridStroke },
    tooltipLine: { lineWidth: 0.5, strokeStyle: rtTipColor },
    labels: { fillStyle: rtLabelColor, fontFamily: "Courier New", fontSize: 10 },
    maxValue: 100,
    minValue: 0,
  });

  const cpuChart = new SmoothieChart({
    responsive: true,
    yMinFormatter: () => "",
    yMaxFormatter: () => `${cpuPercentRef.value.toFixed(1)}% CPU`,
    millisPerPixel: 100,
    grid: { lineWidth: 1, millisPerLine: 500, verticalSections: 1, fillStyle: rtGridFill, strokeStyle: rtGridStroke },
    tooltipLine: { lineWidth: 0.5, strokeStyle: rtTipColor },
    labels: { fillStyle: rtLabelColor, fontFamily: "Courier New", fontSize: 10 },
    maxValue: 100,
    minValue: 0,
  });

  const netChart = new SmoothieChart({
    responsive: true,
    yMinFormatter: () => `${bytesToHuman(netDownRef.value)}/s ⇣`,
    yMaxFormatter: () => `${bytesToHuman(netUpRef.value)}/s ⇡`,
    millisPerPixel: 100,
    grid: { lineWidth: 1, millisPerLine: 500, verticalSections: 2, fillStyle: rtGridFill, strokeStyle: rtGridStroke },
    tooltipLine: { lineWidth: 0.5, strokeStyle: rtTipColor },
    labels: { fillStyle: rtLabelColor, fontFamily: "Courier New", fontSize: 10 },
    minValue: 0,
  });

  const mem = new TimeSeries();
  const memBuffers = new TimeSeries();
  const memCached = new TimeSeries();
  const memShared = new TimeSeries();

  const cpu = new TimeSeries();
  const cpuIdle = new TimeSeries();
  const cpuUser = new TimeSeries();
  const cpuSystem = new TimeSeries();

  const netDown = new TimeSeries();
  const netUp = new TimeSeries();

  memChart.addTimeSeries(mem, { lineWidth: 1, strokeStyle: "#3498db", fillStyle: "rgba(52, 152, 219, 0.1)", interpolation: "bezier" });
  memChart.addTimeSeries(memBuffers, { lineWidth: 0.5, strokeStyle: "#f1c40f", fillStyle: "rgba(241, 196, 15, 0.01)", interpolation: "bezier" });
  memChart.addTimeSeries(memCached, { lineWidth: 0.5, strokeStyle: "#3a4899", fillStyle: "rgba(58, 72, 153, 0.01)", interpolation: "bezier" });
  memChart.addTimeSeries(memShared, { lineWidth: 0.5, strokeStyle: "#e74c3c", fillStyle: "rgba(231, 76, 60, 0.01)", interpolation: "bezier" });

  cpuChart.addTimeSeries(cpu, { lineWidth: 1.2, strokeStyle: "#00ff00", fillStyle: "rgba(0, 255, 0, 0.1)", interpolation: "bezier" });
  cpuChart.addTimeSeries(cpuIdle, { lineWidth: 0.5, strokeStyle: "#979a9a", fillStyle: "rgba(151, 154, 154, 0.01)", interpolation: "bezier" });
  cpuChart.addTimeSeries(cpuUser, { lineWidth: 0.5, strokeStyle: "#f39c12", fillStyle: "rgba(243, 156, 18, 0.01)", interpolation: "bezier" });
  cpuChart.addTimeSeries(cpuSystem, { lineWidth: 0.5, strokeStyle: "#2e86c1", fillStyle: "rgba(46, 134, 193, 0.01)", interpolation: "bezier" });

  netChart.addTimeSeries(netDown, {
    lineWidth: 1.2,
    strokeStyle: C.netDown,
    fillStyle: "rgba(112, 191, 86, 0.08)",
    interpolation: "bezier",
  });
  netChart.addTimeSeries(netUp, {
    lineWidth: 0.8,
    strokeStyle: C.netUp,
    fillStyle: "rgba(203, 77, 28, 0.1)",
    interpolation: "bezier",
  });

  memChart.streamTo(rtMemCanvasRef.value);
  cpuChart.streamTo(rtCpuCanvasRef.value);
  netChart.streamTo(rtNetCanvasRef.value);

  realtimeChartsRef.value = { cpu: cpuChart, mem: memChart, net: netChart };
  realtimeSeriesRef.value = {
    cpu,
    cpuIdle,
    cpuUser,
    cpuSystem,
    mem,
    memBuffers,
    memCached,
    memShared,
    netUp,
    netDown,
  };
};

const teardownRealtimeCharts = () => {
  for (const chart of Object.values(realtimeChartsRef.value)) {
    chart.stop();
    for (const series of Object.values(realtimeSeriesRef.value)) chart.removeTimeSeries(series);
  }
  realtimeChartsRef.value = {};
  realtimeSeriesRef.value = {};
};

const updateStaticCharts = () => {
  if (!cpuChartRef.value || !resChartRef.value || !memChartRef.value) return;
  const points = historyPoints.value;
  const axisTextColor = isDarkMode.value ? "#9CA3AF" : "#6B7280";
  const axisLineColor = isDarkMode.value ? "rgba(148, 163, 184, 0.35)" : "rgba(100, 116, 139, 0.3)";
  const splitLineColor = isDarkMode.value ? "rgba(148, 163, 184, 0.22)" : "rgba(100, 116, 139, 0.18)";
  const titleColor = isDarkMode.value ? "#9CA3AF" : "#707B7C";

  const xData = points.map((p) => {
    const dt = new Date(statusTimestampToMs(p.timestamp));
    const m = String(dt.getMonth() + 1).padStart(2, "0");
    const da = String(dt.getDate()).padStart(2, "0");
    const h = String(dt.getHours()).padStart(2, "0");
    const mi = String(dt.getMinutes()).padStart(2, "0");
    return `${m}/${da} ${h}:${mi}`;
  });

  const cpuSeries = points.map((p, i) => [xData[i], Number(p.cpu_percent || 0)]);
  const userSeries = points.map((p, i) => [xData[i], Number(p.cpu_times_user || 0)]);
  const systemSeries = points.map((p, i) => [xData[i], Number(p.cpu_times_system || 0)]);
  const tempSeries = points.map((p, i) => [xData[i], Number(p.core_temperature || 0)]);

  const freeSeries = points.map((p, i) => [xData[i], bytesToMB(p.mem_free)]);
  const bufferSeries = points.map((p, i) => [xData[i], bytesToMB(p.mem_buffers)]);
  const cachedSeries = points.map((p, i) => [xData[i], bytesToMB(p.mem_cached)]);
  const availableSeries = points.map((p, i) => [xData[i], bytesToMB(p.mem_available)]);

  const fileSeries = points.map((p, i) => [xData[i], Number(p.fd_count || 0)]);
  const tcpSeries = points.map((p, i) => [xData[i], Number(p.tcpcon_count || 0)]);
  const udpSeries = points.map((p, i) => [xData[i], Number(p.udpcon_count || 0)]);
  const threadSeries = points.map((p, i) => [xData[i], Number(p.thread_count || 0)]);
  const crashSeries = points.map((p, i) => [xData[i], Number(p.crash_count || 0)]);

  const getChart = (key: "cpu" | "resource" | "memory", dom: HTMLDivElement) => {
    const curr = staticChartsRef.value[key];
    if (curr) return curr;
    const c = echarts.init(dom);
    staticChartsRef.value[key] = c;
    return c;
  };

  const cpuChart = getChart("cpu", cpuChartRef.value);
  const resourceChart = getChart("resource", resChartRef.value);
  const memoryChart = getChart("memory", memChartRef.value);

  cpuChart.setOption({
    animation: true,
    color: [C.cpu, C.cpuUser, C.cpuSystem, C.cpuTemp],
    legend: { data: ["CPU", "User", "System", "Temp"], left: "right", textStyle: { color: axisTextColor } },
    tooltip: { show: true, trigger: "item" },
    xAxis: [{ data: xData, axisLabel: { color: axisTextColor }, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { show: true, lineStyle: { color: splitLineColor } } }],
    yAxis: [{ axisLabel: { color: axisTextColor }, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { show: true, lineStyle: { color: splitLineColor } } }],
    title: [{ text: "CPU", left: "left", textStyle: { fontSize: 18, color: titleColor } }],
    dataZoom: [{ show: true, type: "inside", start: 0, end: 100 }],
    grid: { left: 50, right: 10, top: 50, bottom: 20 },
    series: [
      { type: "line", name: "CPU", data: cpuSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.25 } },
      { type: "line", name: "User", data: userSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.25 } },
      { type: "line", name: "System", data: systemSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.25 } },
      { type: "line", name: "Temp", data: tempSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.12 } },
    ],
  }, { notMerge: true });

  resourceChart.setOption({
    animation: true,
    legend: {
      data: ["File", "TCP", "UDP", "Thread", "Crash"],
      selected: { File: false, Thread: false, Crash: false },
      left: "right",
      textStyle: { color: axisTextColor },
    },
    tooltip: { show: true, trigger: "item" },
    xAxis: [{ data: xData, axisLabel: { color: axisTextColor }, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { show: true, lineStyle: { color: splitLineColor } } }],
    yAxis: [{ axisLabel: { color: axisTextColor }, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { show: true, lineStyle: { color: splitLineColor } } }],
    title: [{ text: "RES", left: "left", textStyle: { fontSize: 18, color: titleColor } }],
    dataZoom: [{ show: true, type: "inside", start: 0, end: 100 }],
    grid: { left: 50, right: 10, top: 50, bottom: 20 },
    series: [
      { type: "line", name: "File", data: fileSeries, lineStyle: { width: 1 } },
      { type: "line", name: "TCP", data: tcpSeries, lineStyle: { width: 1 } },
      { type: "line", name: "UDP", data: udpSeries, lineStyle: { width: 1 } },
      { type: "line", name: "Thread", data: threadSeries, lineStyle: { width: 1 } },
      { type: "line", name: "Crash", data: crashSeries, lineStyle: { width: 1 } },
    ],
  }, { notMerge: true });

  memoryChart.setOption({
    animation: true,
    legend: { data: ["free", "buffer", "cached", "available"], left: "right", textStyle: { color: axisTextColor } },
    tooltip: { show: true, trigger: "item" },
    xAxis: [{ data: xData, axisLabel: { color: axisTextColor }, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { show: true, lineStyle: { color: splitLineColor } } }],
    yAxis: [{ axisLabel: { color: axisTextColor }, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { show: true, lineStyle: { color: splitLineColor } } }],
    title: [{ text: "MEM", left: "left", textStyle: { fontSize: 18, color: titleColor } }],
    dataZoom: [{ show: true, type: "inside", start: 0, end: 100 }],
    grid: { left: 50, right: 10, top: 50, bottom: 20 },
    series: [
      { type: "line", name: "free", data: freeSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.25 } },
      { type: "line", name: "buffer", data: bufferSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.25 } },
      { type: "line", name: "cached", data: cachedSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.25 } },
      { type: "line", name: "available", data: availableSeries, lineStyle: { width: 1 }, areaStyle: { opacity: 0.25 } },
    ],
  }, { notMerge: true });
};

const disposeStaticCharts = () => {
  Object.values(staticChartsRef.value).forEach((chart) => chart?.dispose());
  staticChartsRef.value = {};
};

const resizeStaticCharts = () => {
  staticChartsRef.value.cpu?.resize();
  staticChartsRef.value.resource?.resize();
  staticChartsRef.value.memory?.resize();
};


function appendSample(data: DeviceStatusPoint, tsMs: number) {
  const sent = Number(data.net_io_bytes_sent || 0);
  const recv = Number(data.net_io_bytes_recv || 0);

  const cpu = Number(data.cpu_percent || 0);
  const mem = Number(data.mem_percent || 0);
  cpuPercentRef.value = cpu;
  memPercentRef.value = mem;
  realtimeUsage.value = { cpu, mem };

  const prev = prevNetCounterRef.value;
  const realtimeSeries = realtimeSeriesRef.value;
  if (realtimeSeries.cpu) {
    realtimeSeries.cpu.append(tsMs, cpu);
    realtimeSeries.cpuIdle?.append(tsMs, Number(data.cpu_times_idle || 0));
    realtimeSeries.cpuUser?.append(tsMs, Number(data.cpu_times_user || 0));
    realtimeSeries.cpuSystem?.append(tsMs, Number(data.cpu_times_system || 0));

    realtimeSeries.mem?.append(tsMs, mem);
    realtimeSeries.memBuffers?.append(tsMs, Number(data.mem_buffers || 0));
    realtimeSeries.memCached?.append(tsMs, Number(data.mem_cached || 0));
    realtimeSeries.memShared?.append(tsMs, Number(data.mem_shared || 0));
  }

  if (prev && tsMs > prev.tsMs && realtimeSeries.netUp && realtimeSeries.netDown) {
    const up = Math.max(0, sent - prev.sent) * 1000 / (tsMs - prev.tsMs);
    const down = Math.max(0, recv - prev.recv) * 1000 / (tsMs - prev.tsMs);

    netUpRef.value = up;
    netDownRef.value = down;
    latestRate.value = { up, down };

    realtimeSeries.netUp.append(tsMs, up);
    realtimeSeries.netDown.append(tsMs, down);
  }

  prevNetCounterRef.value = { sent, recv, tsMs };
}
const clearSendTimer = () => {
  if (wsTimer !== null) {
    window.clearInterval(wsTimer);
    wsTimer = null;
  }
};

const setupWebSocket = () => {
  if (!props.deviceId) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/d/${encodeURIComponent(props.deviceId)}/ws/command`;
  const ws = new WebSocket(wsUrl);
  wsInstance = ws;

  ws.onopen = () => {
    clearSendTimer();
    wsTimer = window.setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("status");
    }, 1000);
  };

  ws.onmessage = (evt) => {
    try {
      const payload = JSON.parse(String(evt.data)) as { type?: string; data?: DeviceStatusPoint; timestamp?: number };
      if (payload.data && payload.data.cpu_percent != null) appendSample(payload.data, statusTimestampToMs(payload.timestamp) || Date.now());
    } catch {
      // ignore malformed ws payloads
    }
  };

  ws.onclose = () => {
    clearSendTimer();
  };
};

const teardownWebSocket = () => {
  clearSendTimer();
  if (wsInstance) {
    wsInstance.onopen = wsInstance.onclose = wsInstance.onmessage = null;
    wsInstance.close();
    wsInstance = null;
  }
};


async function loadHistory() {
  try {
    const { data } = await apiRequest<unknown>(`/api/v1/device/${encodeURIComponent(props.deviceId)}/status?limit=2048`, {
      signal: historyRequest.signal,
      errorMessage: "读取历史性能数据失败",
    });
    if (historyRequest.signal.aborted) return;
    const points = extractArray<DeviceStatusPoint>(data).sort((a, b) => statusTimestampToMs(a.timestamp) - statusTimestampToMs(b.timestamp));
    historyPoints.value = points;
    if (!prevNetCounterRef.value) {
      for (const sample of points.slice(-2)) appendSample(sample, statusTimestampToMs(sample.timestamp));
    }
  } catch (err) {
    if (!historyRequest.signal.aborted) loadError.value = formatApiError(err, "读取历史性能数据失败");
  } finally {
    loading.value = false;
  }
}

watch(() => props.sampledAt, ts => {
  if (ts && props.liveStatus) appendSample(props.liveStatus, ts);
});
watch(isDarkMode, () => {
  teardownRealtimeCharts();
  setupRealtimeCharts();
});
watch([historyPoints, isDarkMode], updateStaticCharts, { flush: "post" });

onMounted(() => {
  setupRealtimeCharts();
  if (props.liveStatus === undefined) setupWebSocket();
  else if (props.sampledAt) appendSample(props.liveStatus, props.sampledAt);
  themeObserver = new MutationObserver(() => { isDarkMode.value = document.documentElement.classList.contains("dark"); });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  resizeObserver = new ResizeObserver(resizeStaticCharts);
  if (container.value) resizeObserver.observe(container.value);
  void loadHistory();
});

onBeforeUnmount(() => {
  historyRequest.abort();
  resizeObserver?.disconnect();
  themeObserver?.disconnect();
  teardownRealtimeCharts();
  teardownWebSocket();
  disposeStaticCharts();
});
</script>

<template>
  <div ref="container" class="performance-charts">
    <p v-if="loadError" role="alert" class="col-span-full text-sm text-destructive">{{ loadError }}</p>
    <Card class="flex flex-col overflow-hidden">
      <CardHeader class="shrink-0 px-3 pb-0 pt-0">
        <div class="flex items-start justify-between gap-2">
          <CardTitle class="text-sm font-semibold">{{ t.deviceDetail.charts.realtime }}</CardTitle>
          <div class="flex flex-col items-end gap-0.5 text-[10px] font-mono">
            <span class="text-muted-foreground">
              <span class="font-semibold text-foreground">{{ realtimeUsage.cpu.toFixed(1) }}%</span> CPU
              <span class="font-semibold text-foreground">{{ realtimeUsage.mem.toFixed(1) }}%</span> MEM
            </span>
            <span>
              <span class="font-medium" :style="{ color: C.netUp }">{{ bytesToHuman(latestRate.up) }}/s ↑</span>
              <span class="font-medium" :style="{ color: C.netDown }">{{ bytesToHuman(latestRate.down) }}/s ↓</span>
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent class="relative min-h-0 flex-1 px-1 pb-0 pt-0">
        <div class="flex h-full flex-col">
          <div class="flex h-1/2">
            <canvas ref="rtCpuCanvasRef" class="h-full w-1/2" />
            <canvas ref="rtMemCanvasRef" class="h-full w-1/2" />
          </div>
          <div class="h-1/2">
            <canvas ref="rtNetCanvasRef" class="h-full w-full" />
          </div>
        </div>
        <div v-if="loading" class="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div class="rounded-md border border-border bg-background px-3 py-2 text-xs text-muted-foreground">{{ t.common.loading }}</div>
        </div>
      </CardContent>
    </Card>

    <Card class="flex flex-col overflow-hidden">
      <CardContent class="min-h-0 flex-1 p-0">
        <div v-if="hasTrendData" ref="cpuChartRef" class="h-full w-full" />
        <div v-else class="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
          <Activity class="h-6 w-6" />
          <p class="text-xs">{{ t.devices.noData }}</p>
        </div>
      </CardContent>
    </Card>

    <Card class="flex flex-col overflow-hidden">
      <CardContent class="min-h-0 flex-1 p-0">
        <div v-if="hasTrendData" ref="resChartRef" class="h-full w-full" />
        <div v-else class="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
          <Activity class="h-6 w-6" />
          <p class="text-xs">{{ t.devices.noData }}</p>
        </div>
      </CardContent>
    </Card>

    <Card class="flex flex-col overflow-hidden">
      <CardContent class="min-h-0 flex-1 p-0">
        <div v-if="hasTrendData" ref="memChartRef" class="h-full w-full" />
        <div v-else class="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
          <Activity class="h-6 w-6" />
          <p class="text-xs">{{ t.devices.noData }}</p>
        </div>
      </CardContent>
    </Card>
  </div>
</template>

<style scoped>
.performance-charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-auto-rows: minmax(240px, 1fr);
  gap: 12px;
  container-type: inline-size;
}
.performance-charts > p { align-self: center; }
@container (max-width: 690px) {
  .performance-charts > * { grid-column: 1 / -1; }
}
</style>
