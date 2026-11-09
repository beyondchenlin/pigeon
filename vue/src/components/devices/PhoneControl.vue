<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ArrowLeft, Circle, Square, RotateCw, Power, Pause, Play, Maximize, Minimize } from 'lucide-vue-next';
import { deviceSocket } from '@/lib/device-channel';
import { startPhoneVideo, type VideoMode } from '@/lib/phone-video';
import { Button } from '@/components/ui/button';

const props = defineProps<{ deviceId: string; online: boolean; commandReady: boolean; rotation?: number }>();
const emit = defineEmits<{ command: [command: string, data: unknown]; log: [message: string] }>();
const canvas = ref<HTMLCanvasElement>();
const panel = ref<HTMLDivElement>();
const viewport = ref<HTMLDivElement>();
const viewportSize = ref({ width: 320, height: 600 });
const fullscreen = ref(false);
const fullscreenError = ref('');
const canFullscreen = typeof document.documentElement.requestFullscreen === 'function' && document.fullscreenEnabled;
let resizeObserver: ResizeObserver | undefined;
const state = ref('connecting');
const touchReady = ref(false);
const frames = ref(0);
const fps = ref(0);
const paused = ref(false);
const visible = ref(!document.hidden);
const mode = ref<VideoMode>('h264');
const mediaRtt = ref<number | null>(null);
const mediaPath = ref('unknown');
const screenSize = ref({ width: 540, height: 1194 });
const canvasStyle = computed(() => {
  const ratio = screenSize.value.width / screenSize.value.height;
  const width = Math.max(1, Math.min(viewportSize.value.width, viewportSize.value.height * ratio));
  return { width: `${width}px`, height: `${width / ratio}px` };
});
const enabled = computed(() => props.online && visible.value && !paused.value);
let dispose: (() => void) | undefined;
let retry: ReturnType<typeof setTimeout> | undefined;
let touch: WebSocket | undefined;
let dimensions = { width: 0, height: 0 };
let pointer: number | null = null;
let generation = 0;
let touchDispose: (() => void) | undefined;
let frameTimer: ReturnType<typeof setInterval> | undefined;
let moveRaf = 0;
let lastMove: PointerEvent | undefined;

function release() {
  cancelAnimationFrame(moveRaf); moveRaf = 0; lastMove = undefined;
  if (pointer !== null && touch?.readyState === WebSocket.OPEN) touch.send(JSON.stringify({ action: 'up', id: 0 }));
  pointer = null;
}
function stop() {
  generation++; release(); clearTimeout(retry); clearInterval(frameTimer);
  touchDispose?.(); touchDispose = undefined; dispose?.(); dispose = undefined;
  touch = undefined; touchReady.value = false; fps.value = 0; mediaRtt.value = null; mediaPath.value = 'unknown';
}
function start() {
  stop();
  if (!enabled.value || !canvas.value) { state.value = 'paused'; return; }
  const own = generation;
  let count = 0, measured = performance.now(), touchAttempts = 0;
  const valid = () => own === generation && enabled.value;
  const connectTouch = () => {
    if (!valid()) return;
    touchDispose?.();
    const ws = new WebSocket(deviceSocket(props.deviceId, '/ws/screen/touch')); touch = ws;
    let closed = false;
    touchDispose = () => { closed = true; ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null; ws.close(); };
    const recover = () => {
      if (closed || !valid()) return; release(); touchReady.value = false; touchDispose?.();
      emit('log', '触控通道中断，正在恢复');
      retry = setTimeout(connectTouch, Math.min(8000, 300 * 2 ** Math.min(touchAttempts++, 5)));
    };
    ws.onopen = () => { if (valid()) ws.send(JSON.stringify({ action: 'reset' })); };
    ws.onmessage = e => { if (!valid()) return; try { const m = JSON.parse(e.data); if (m.type === 'OK' && m.w > 0 && m.h > 0) { dimensions = { width: m.w, height: m.h }; touchReady.value = true; touchAttempts = 0; } } catch {} };
    ws.onclose = ws.onerror = recover;
  };
  connectTouch();
  dispose = startPhoneVideo({
    url: path => deviceSocket(props.deviceId, path), canvas: canvas.value,
    decoderUrl: new URL(`/d/${encodeURIComponent(props.deviceId)}/static/js/decoder.js`, location.href).href,
    webrtc: new URLSearchParams(location.search).get('transport') !== 'ws',
    frame: (width, height) => { if (valid()) { screenSize.value = { width, height }; state.value = 'live'; frames.value++; count++; } },
    state: value => { if (valid()) state.value = value; },
    mode: value => { if (valid()) mode.value = value; },
    log: message => { if (valid()) emit('log', message); },
    stats: value => { if (valid()) { mediaRtt.value = value.rttMs; mediaPath.value = value.path; } }
  });
  frameTimer = setInterval(() => { const now = performance.now(); fps.value = Math.round(count * 1000 / (now - measured)); measured = now; count = 0; }, 2000);
}
function send(action: string, event?: PointerEvent) {
  if (!touchReady.value || touch?.readyState !== WebSocket.OPEN || state.value !== 'live') return false;
  let payload: Record<string, unknown> = { action, id: 0 };
  if (event && canvas.value) {
    const rect = canvas.value.getBoundingClientRect();
    const u = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const v = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    const rot = (props.rotation ?? 0) % 4;
    const [x, y] = rot === 1 ? [1-v, u] : rot === 2 ? [1-u, 1-v] : rot === 3 ? [v, 1-u] : [u, v];
    payload = { ...payload, x: Math.min(dimensions.width - 1, Math.floor(x * dimensions.width)), y: Math.min(dimensions.height - 1, Math.floor(y * dimensions.height)) };
  }
  touch.send(JSON.stringify(payload)); return true;
}
function down(e: PointerEvent) { if (e.button !== 0 || pointer !== null) return; if (send('down', e)) { pointer = e.pointerId; canvas.value?.setPointerCapture(e.pointerId); } }
function move(e: PointerEvent) {
  if (pointer !== e.pointerId) return; lastMove = e;
  if (!moveRaf) moveRaf = requestAnimationFrame(() => { moveRaf = 0; if (lastMove && (touch?.bufferedAmount ?? 0) < 32768) send('move', lastMove); lastMove = undefined; });
}
function up(e: PointerEvent) { if (pointer !== e.pointerId) return; release(); if (canvas.value?.hasPointerCapture(e.pointerId)) canvas.value.releasePointerCapture(e.pointerId); }
function key(code: number) { emit('command', 'device/input/keycode', [0, code]); }
function visibility() { visible.value = !document.hidden; }
function fullscreenChanged() { fullscreen.value = document.fullscreenElement === panel.value; }
function fullscreenKey(event: KeyboardEvent) {
  if (event.key === 'Escape' && document.fullscreenElement === panel.value) {
    void document.exitFullscreen().catch(() => { fullscreenError.value = '退出全屏失败，请点击退出按钮重试。'; });
  }
}
async function toggleFullscreen() {
  fullscreenError.value = '';
  try {
    if (document.fullscreenElement === panel.value) await document.exitFullscreen();
    else await panel.value?.requestFullscreen();
  } catch { fullscreenError.value = '浏览器未允许全屏，请重试。'; emit('log', fullscreenError.value); }
}
watch(enabled, start);
watch(() => props.rotation, start);
onMounted(() => {
  document.addEventListener('visibilitychange', visibility);
  document.addEventListener('fullscreenchange', fullscreenChanged);
  document.addEventListener('keydown', fullscreenKey);
  resizeObserver = new ResizeObserver(entries => {
    const rect = entries[0]?.contentRect;
    if (rect && rect.width > 0 && rect.height > 0) viewportSize.value = { width: rect.width, height: rect.height };
  });
  if (viewport.value) resizeObserver.observe(viewport.value);
  start();
});
onBeforeUnmount(() => { stop(); resizeObserver?.disconnect(); document.removeEventListener('visibilitychange', visibility); document.removeEventListener('fullscreenchange', fullscreenChanged); document.removeEventListener('keydown', fullscreenKey); });
</script>

<template>
  <div ref="panel" class="phone-control overflow-hidden rounded-lg border border-border bg-background">
    <div class="phone-heading flex items-center justify-between gap-2 border-b px-3 text-xs">
      <span class="font-semibold">实时画面</span>
      <div class="flex items-center gap-2"><span class="mr-1 text-muted-foreground">{{ mode === 'webrtc' ? 'WebRTC' : mode === 'h264' ? 'H.264' : '兼容模式' }}</span>
        <button class="screen-action" :title="paused ? '恢复画面' : '暂停画面'" :aria-label="paused ? '恢复画面' : '暂停画面'" @click="paused = !paused"><component :is="paused ? Play : Pause" class="h-4 w-4" /></button>
        <button v-if="canFullscreen" class="screen-action" :title="fullscreen ? '退出全屏' : '全屏查看'" :aria-label="fullscreen ? '退出全屏' : '全屏查看'" @click="toggleFullscreen"><component :is="fullscreen ? Minimize : Maximize" class="h-4 w-4" /></button>
      </div>
    </div>
    <div ref="viewport" class="screen-viewport relative flex items-center justify-center bg-black p-2" :data-control-state="state" :data-control-frames="frames" :data-video-transport="mode" :data-media-rtt="mediaRtt" :data-media-path="mediaPath">
      <canvas ref="canvas" :style="canvasStyle" class="block touch-none select-none" aria-label="手机实时远控画面" @pointerdown.prevent="down" @pointermove.prevent="move" @pointerup="up" @pointercancel="up" @lostpointercapture="release" @contextmenu.prevent />
      <div v-if="state !== 'live'" class="absolute inset-0 flex items-center justify-center bg-black/75 px-4 text-center text-sm text-white">{{ !online ? '设备离线，等待重新连接' : paused || !visible ? '画面已暂停' : state === 'reconnecting' ? '画面重连中…' : '正在连接手机画面…' }}</div>
    </div>
    <div class="phone-navigation border-t" role="group" aria-label="手机操作">
      <Button variant="ghost" class="nav-action" aria-label="返回手机上一页" :disabled="!commandReady" @click="key(4)"><ArrowLeft class="h-4 w-4" /><span>返回</span></Button>
      <Button variant="ghost" class="nav-action" aria-label="手机主页" :disabled="!commandReady" @click="key(3)"><Circle class="h-4 w-4" /><span>主页</span></Button>
      <Button variant="ghost" class="nav-action" aria-label="手机最近任务" :disabled="!commandReady" @click="key(187)"><Square class="h-4 w-4" /><span>最近任务</span></Button>
      <Button variant="ghost" class="nav-action" aria-label="唤醒手机" :disabled="!commandReady" @click="emit('command', 'device/wakeup', [])"><Power class="h-4 w-4" /><span>唤醒</span></Button>
      <Button variant="ghost" class="nav-action" aria-label="重连画面" @click="paused = false; start()"><RotateCw class="h-4 w-4" /><span>重连</span></Button>
    </div>
    <p class="phone-hint text-center text-[11px] text-muted-foreground">{{ touchReady ? '点击或拖动手机画面进行操作' : '触控通道尚未连接' }}</p>
    <p v-if="fullscreenError" role="alert" class="px-3 pb-2 text-xs text-amber-700">{{ fullscreenError }}</p>
    <div class="phone-status border-t text-muted-foreground"><span>{{ commandReady ? '控制通道已连接' : '控制通道未连接' }}</span><span title="浏览器最近两秒实际绘制的帧数，静止画面可能为 0；不是请求帧率">帧率 {{ state === 'live' ? `${fps} FPS` : '—' }}</span><span title="WebRTC 媒体通道往返时间，不是点击到画面变化的端到端延迟">媒体 RTT {{ mediaRtt == null ? '—' : `${mediaRtt} ms` }}</span></div>
  </div>
</template>

<style scoped>
.phone-control { display: flex; flex-direction: column; min-width: 0; }
.phone-heading { min-height: 42px; flex-shrink: 0; }
.screen-action { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 4px; }
.screen-action:hover { background: var(--muted); }
.screen-viewport { flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.phone-navigation { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 2px; padding: 5px 7px 0; flex-shrink: 0; }
.phone-navigation .nav-action { height: 46px; padding: 4px 0; gap: 4px; flex-direction: column; font-size: 10px; line-height: 14px; font-weight: 400; }
.phone-hint { padding: 5px 8px 9px; flex-shrink: 0; }
.phone-status { display: flex; flex-wrap: wrap; gap: 3px 12px; padding: 8px 12px; font-size: 10px; line-height: 14px; flex-shrink: 0; }
.phone-control:fullscreen { width: 100vw; height: 100dvh; max-width: none; border-radius: 0; }
.phone-control:fullscreen .phone-navigation { width: min(440px, 100%); align-self: center; }
.phone-control:fullscreen .phone-status { justify-content: center; }
.screen-action:focus-visible { outline: 2px solid #f97316; outline-offset: 2px; }
</style>
