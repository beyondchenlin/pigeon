import { computed, onBeforeUnmount, onMounted, ref, type Ref, watch } from 'vue';
import { apiRequest } from './api';

export type Telemetry = {
  sampled_at?: number; stale?: boolean; error?: string;
  battery?: { percent: number | null; charging: boolean | null; status: number | null; health: number | null;
    temperature_c: number | null; voltage_v: number | null; technology?: string; source?: string };
  storage?: { total?: number; used?: number; available?: number };
  display?: { width?: number; height?: number; density?: number };
  system?: { manufacturer?: string; model?: string; android?: string; sdk?: string; abi?: string };
};

// No fallback to the registration record: older phone agents omit battery data.
export function useDeviceTelemetry(id: Ref<string>, enabled: Ref<boolean>) {
  const data = ref<Telemetry>({});
  const now = ref(Date.now());
  const loading = ref(false);
  const pageVisible = ref(!document.hidden);
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;
  let generation = 0;
  let lastAttempt = 0;
  const active = computed(() => enabled.value && pageVisible.value);
  const stale = computed(() => !active.value || data.value.stale || !data.value.sampled_at || now.value / 1000 - data.value.sampled_at > 35);
  const sampledTime = computed(() => data.value.sampled_at ? new Date(data.value.sampled_at * 1000).toLocaleTimeString() : '—');

  async function refresh() {
    if (!active.value || loading.value) return;
    const gen = generation;
    const requestController = new AbortController();
    controller = requestController;
    lastAttempt = Date.now();
    loading.value = true;
    const timeout = setTimeout(() => requestController.abort(), 15000);
    try {
      const result = await apiRequest<Telemetry>(`/api/v1/device/${encodeURIComponent(id.value)}/telemetry`, { signal: requestController.signal, cache: 'no-store' });
      if (gen === generation) {
        data.value = result.data?.sampled_at ? result.data : { ...data.value, stale: true, error: result.data?.error || 'unavailable' };
      }
    } catch {
      if (gen === generation) data.value = { ...data.value, stale: true, error: 'unavailable' };
    } finally {
      clearTimeout(timeout);
      if (gen === generation) loading.value = false;
    }
  }
  function reset() {
    generation++;
    controller?.abort();
    loading.value = false;
    void refresh();
  }
  function visibility() { pageVisible.value = !document.hidden; }
  watch(active, reset);
  watch(id, () => { data.value = {}; reset(); });
  onMounted(() => {
    document.addEventListener('visibilitychange', visibility);
    void refresh();
    timer = setInterval(() => { now.value = Date.now(); if (now.value - lastAttempt >= 15000) void refresh(); }, 1000);
  });
  onBeforeUnmount(() => { generation++; controller?.abort(); clearInterval(timer); document.removeEventListener('visibilitychange', visibility); });
  return { data, loading, stale, sampledTime, refresh };
}

export function bytes(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  return `${(value / 1024 ** 2).toFixed(0)} MB`;
}
