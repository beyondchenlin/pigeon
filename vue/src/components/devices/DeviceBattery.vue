<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRef } from 'vue';
import { Battery, BatteryCharging } from 'lucide-vue-next';
import { useDeviceTelemetry } from '@/lib/device-telemetry';
const props = defineProps<{ deviceId: string; online: boolean }>();
const host = ref<HTMLElement>();
const visible = ref(false);
let observer: IntersectionObserver;
const active = computed(() => visible.value && props.online);
const { data, stale, sampledTime } = useDeviceTelemetry(toRef(props, 'deviceId'), active);
onMounted(() => { observer = new IntersectionObserver(([e]) => { visible.value = e.isIntersecting; }); if (host.value) observer.observe(host.value); });
onBeforeUnmount(() => observer?.disconnect());
</script>
<template>
  <span ref="host" class="inline-flex items-center gap-1 text-xs tabular-nums" :class="stale ? 'text-muted-foreground' : 'text-emerald-600'"
    :title="`手机系统采集：${sampledTime}${stale ? '，未取得最新数据' : ''}`" :data-battery-stale="stale" :data-battery-percent="data.battery?.percent">
    <component :is="data.battery?.charging ? BatteryCharging : Battery" class="h-3.5 w-3.5" />
    {{ data.battery?.percent == null ? '未知' : `${data.battery.percent}%` }}<span v-if="stale && data.battery?.percent != null">· 过期</span>
  </span>
</template>
