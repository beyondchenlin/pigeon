<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ArrowLeft, Circle, Square, RotateCw, Power, Pause, Play, Maximize, Minimize, MessageSquare, MoreHorizontal } from 'lucide-vue-next';
import { deviceSocket } from '@/lib/device-channel';
import { startPhoneVideo, type VideoMode } from '@/lib/phone-video';
import { androidKey, createPhoneInput, type PhoneRequest } from '@/lib/phone-input';
import { apiRequest } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { fitDisplay } from '@/lib/mobile-layout';
import { createPhoneTouch, type TouchPayload } from '@/lib/phone-touch';
import { phoneFrame } from '@/lib/phone-frame';
import { type VideoPreference, type VideoProfile } from '@/lib/video-policy';

const props = defineProps<{ deviceId: string; online: boolean; commandReady: boolean; requestCommand: PhoneRequest; rotation?: number; immersive?: boolean; controlsVisible?: boolean; textBusy?: boolean; videoPreference?: VideoPreference }>();
const emit = defineEmits<{ command: [command: string, data: unknown]; log: [message: string]; 'hide-tools': []; compose: []; tools: [] }>();
const canvas = ref<HTMLCanvasElement>();
const video = ref<HTMLVideoElement>();
// Diagnostic rollback without changing the controls or the device service.
const nativeVideo = new URLSearchParams(location.search).get('renderer') !== 'canvas';
const panel = ref<HTMLDivElement>();
const viewport = ref<HTMLDivElement>();
const keyboard = ref<HTMLTextAreaElement>();
const composing = ref(false);
const input = createPhoneInput((cmd, data) => props.requestCommand(cmd, data), () => emit('log', '键盘操作未完成，请确认输入框、连接及浏览器剪贴板权限后重试'), text => navigator.clipboard.writeText(text));
function inputText() {
  if (props.textBusy || composing.value || !keyboard.value) return;
  dispose?.interact();
  if (props.commandReady) input.insert(keyboard.value.value);
  keyboard.value.value = '';
}
function keyboardDown(e: KeyboardEvent) {
  dispose?.interact();
  if (props.textBusy) { e.preventDefault(); return; }
  if (!e.isComposing && e.keyCode !== 229 && (e.ctrlKey || e.metaKey) && !e.altKey && ['c', 'x'].includes(e.key.toLowerCase()) && props.commandReady) {
    e.preventDefault(); input.copy(e.key.toLowerCase() === 'x'); return;
  }
  const mapped = androidKey(e);
  if (mapped && props.commandReady) { e.preventDefault(); input.key(...mapped); }
}
function paste(e: ClipboardEvent) {
  dispose?.interact();
  e.preventDefault();
  if (props.commandReady && !props.textBusy) input.insert(e.clipboardData?.getData('text/plain') ?? '');
}
function blurInput() { composing.value = false; input.reset(); if (keyboard.value) keyboard.value.value = ''; }
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
const detailFrame = ref(false);
const videoProfile = ref<VideoProfile>({short:360,fps:10});
const screenSize = ref({ width: 540, height: 1194 });
const canvasStyle = computed(() => {
  const size = fitDisplay(screenSize.value.width, screenSize.value.height, viewportSize.value.width, viewportSize.value.height);
  return { width: `${size.width}px`, height: `${size.height}px` };
});
const enabled = computed(() => props.online && visible.value && !paused.value);
let dispose: ReturnType<typeof startPhoneVideo> | undefined;
let touch: ReturnType<typeof createPhoneTouch> | undefined;
const inputTransport = ref('none');
const inputRtt = ref<number | null>(null);
let dimensions = { width: 0, height: 0 };
let pointer: number | null = null;
let generation = 0;
let frameTimer: ReturnType<typeof setInterval> | undefined;
let moveRaf = 0;
let lastMove: PointerEvent | undefined;

function release() {
  cancelAnimationFrame(moveRaf); moveRaf = 0; lastMove = undefined;
  if (pointer !== null) touch?.send({ action: 'up', id: 0 });
  pointer = null;
}
function stop() {
  generation++; release(); blurInput(); clearInterval(frameTimer);
  touch?.close(); dispose?.(); dispose = undefined;
  touch = undefined; touchReady.value = false; fps.value = 0; mediaRtt.value = null; mediaPath.value = 'unknown';
  inputTransport.value = 'none'; inputRtt.value = null; detailFrame.value = false;
}
function start() {
  stop();
  if (!enabled.value || !canvas.value) { state.value = 'paused'; return; }
  const own = generation;
  let count = 0, measured = performance.now();
  const valid = () => own === generation && enabled.value;
  touch = createPhoneTouch({ url: deviceSocket(props.deviceId, '/ws/screen/touch'),
    ready: (ready, size) => { if (valid()) { touchReady.value = ready; if (size) dimensions = size; } },
    lost: () => { cancelAnimationFrame(moveRaf); moveRaf = 0; lastMove = undefined; pointer = null; },
    log: message => { if (valid()) emit('log', message); },
    stats: (route, rtt) => { if (valid()) { inputTransport.value = route; inputRtt.value = rtt; } }
  });
  dispose = startPhoneVideo({
    interactionBusy: () => pointer !== null || input.busy || !!props.textBusy,
    url: path => deviceSocket(props.deviceId, path), canvas: canvas.value, video: nativeVideo ? video.value : undefined,
    decoderUrl: new URL(`/d/${encodeURIComponent(props.deviceId)}/static/js/decoder.js`, location.href).href,
    webrtc: new URLSearchParams(location.search).get('transport') !== 'ws',
    preference: props.videoPreference, displaySize: () => dimensions,
    snapshot: (short,signal) => phoneFrame(props.deviceId,short,signal),
    detail: active => { if(valid())detailFrame.value=active; },
    profile: value => { if(valid())videoProfile.value=value; },
    inputChannel: channel => { if (valid() && touch) touch.attach(channel); else channel.close(); },
    loadIceServers: async () => {
      const response = await apiRequest<{ iceServers: RTCIceServer[] }>(`/api/v1/device/${encodeURIComponent(props.deviceId)}/ice`, { signal: AbortSignal.timeout(3000) });
      return response.data?.iceServers ?? [];
    },
    frame: (width, height) => { if (valid()) { screenSize.value = { width, height }; state.value = 'live'; frames.value++; count++; } },
    state: value => { if (valid()) state.value = value; },
    mode: value => { if (valid()) mode.value = value; },
    log: message => { if (valid()) emit('log', message); },
    stats: value => { if (valid()) { mediaRtt.value = value.rttMs; mediaPath.value = value.path; } }
  });
  frameTimer = setInterval(() => { const now = performance.now(); fps.value = Math.round(count * 1000 / (now - measured)); measured = now; count = 0; }, 2000);
}
function send(action: TouchPayload['action'], event?: PointerEvent) {
  if (!touchReady.value || !touch || state.value !== 'live') return false;
  dispose?.interact();
  let payload: TouchPayload = { action, id: 0 };
  if (event && canvas.value) {
    const rect = canvas.value.getBoundingClientRect();
    const u = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const v = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    const rot = (props.rotation ?? 0) % 4;
    const [x, y] = rot === 1 ? [1-v, u] : rot === 2 ? [1-u, 1-v] : rot === 3 ? [v, 1-u] : [u, v];
    payload = { ...payload, x: Math.min(dimensions.width - 1, Math.floor(x * dimensions.width)), y: Math.min(dimensions.height - 1, Math.floor(y * dimensions.height)) };
  }
  return touch.send(payload);
}
function down(e: PointerEvent) {
  if (e.button !== 0 || pointer !== null) return;
  // The first tap is a local UI gesture only. Never forward it into an app.
  if (props.immersive && props.controlsVisible) { emit('hide-tools'); return; }
  if (props.textBusy) return;
  if (input.busy) { input.flush(); emit('log', '文字正在写入手机，请稍后再点击'); return; }
  if (send('down', e)) { keyboard.value?.focus({ preventScroll: true }); pointer = e.pointerId; canvas.value?.setPointerCapture(e.pointerId); }
}
function move(e: PointerEvent) {
  if (pointer !== e.pointerId) return; lastMove = e;
  if (!moveRaf) moveRaf = requestAnimationFrame(() => { moveRaf = 0; if (lastMove && (touch?.bufferedAmount ?? 0) < 32768) send('move', lastMove); lastMove = undefined; });
}
function up(e: PointerEvent) {
  if (pointer !== e.pointerId) return;
  if (lastMove && (touch?.bufferedAmount ?? 0) < 32768) send('move', lastMove);
  release(); if (canvas.value?.hasPointerCapture(e.pointerId)) canvas.value.releasePointerCapture(e.pointerId);
}
function key(code: number) { dispose?.interact(); emit('command', 'device/input/keycode', [0, code]); }
function reconnect() { paused.value = false; start(); }
defineExpose({ reconnect, interact: () => dispose?.interact(), isTyping: () => input.busy, releaseKeyboard: blurInput, fps, mode, mediaRtt, touchReady, videoProfile });
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
watch(() => props.videoPreference, start, {deep:true});
watch(() => props.textBusy, () => dispose?.interact());
let wasConnected = false;
watch(() => props.commandReady, ready => {
  if (!ready) { blurInput(); return; }
  if (wasConnected) { touch?.recover(); dispose?.recover(); }
  wasConnected = true;
});
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
  <div ref="panel" class="phone-control overflow-hidden rounded-lg border border-border bg-background" :class="{ immersive, 'controls-hidden': immersive && !controlsVisible }">
    <div class="phone-heading flex items-center justify-between gap-2 border-b px-3 text-xs">
      <span class="font-semibold">实时画面</span>
      <div class="flex items-center gap-2"><span class="mr-1 text-muted-foreground">{{ mode === 'webrtc' ? 'WebRTC' : mode === 'h264' ? 'H.264' : '兼容模式' }}</span>
        <button class="screen-action" :title="paused ? '恢复画面' : '暂停画面'" :aria-label="paused ? '恢复画面' : '暂停画面'" @click="paused = !paused"><component :is="paused ? Play : Pause" class="h-4 w-4" /></button>
        <button v-if="canFullscreen" class="screen-action" :title="fullscreen ? '退出全屏' : '全屏查看'" :aria-label="fullscreen ? '退出全屏' : '全屏查看'" @click="toggleFullscreen"><component :is="fullscreen ? Minimize : Maximize" class="h-4 w-4" /></button>
      </div>
    </div>
    <div ref="viewport" class="screen-viewport relative flex items-center justify-center bg-background" :data-control-state="state" :data-control-frames="frames" :data-video-transport="mode" :data-media-rtt="mediaRtt" :data-media-path="mediaPath" :data-input-transport="inputTransport" :data-input-rtt="inputRtt">
      <video ref="video" :style="canvasStyle" class="rtc-video" :class="{ 'rtc-visible': nativeVideo && mode === 'webrtc' && state === 'live' }" autoplay muted playsinline disablepictureinpicture aria-hidden="true" />
      <canvas ref="canvas" :style="canvasStyle" :class="{ 'rtc-input': nativeVideo && mode === 'webrtc' && state === 'live', 'detail-frame':detailFrame }" class="block touch-none select-none" aria-label="手机实时远控画面" @pointerdown.prevent="down" @pointermove.prevent="move" @pointerup="up" @pointercancel="up" @lostpointercapture="release" @contextmenu.prevent />
      <textarea ref="keyboard" class="phone-keyboard" aria-label="手机文字输入" autocomplete="off" autocapitalize="off" :spellcheck="false" inputmode="none" @keydown="keyboardDown" @input="inputText" @compositionstart="composing = true" @compositionend="composing = false; inputText()" @paste="paste" @blur="blurInput" />
      <div v-if="state !== 'live'" class="absolute inset-0 flex items-center justify-center bg-black/75 px-4 text-center text-sm text-white">{{ !online ? '设备离线，等待重新连接' : paused || !visible ? '画面已暂停' : state === 'reconnecting' ? '画面重连中…' : '正在连接手机画面…' }}</div>
    </div>
    <div class="phone-navigation border-t" role="group" aria-label="手机操作">
      <Button variant="ghost" class="nav-action" aria-label="返回手机上一页" :disabled="!commandReady || textBusy" @click="key(4)"><ArrowLeft class="h-4 w-4" /><span>返回</span></Button>
      <Button variant="ghost" class="nav-action" aria-label="手机主页" :disabled="!commandReady || textBusy" @click="key(3)"><Circle class="h-4 w-4" /><span>主页</span></Button>
      <Button variant="ghost" class="nav-action" aria-label="手机最近任务" :disabled="!commandReady || textBusy" @click="key(187)"><Square class="h-4 w-4" /><span>最近任务</span></Button>
      <template v-if="immersive"><Button variant="ghost" class="nav-action text-orange-600" aria-label="打开文字输入" :disabled="textBusy" @click="emit('compose')"><MessageSquare class="h-4 w-4" /><span>文字输入</span></Button><Button variant="ghost" class="nav-action" aria-label="打开远控工具" @click="emit('tools')"><MoreHorizontal class="h-4 w-4" /><span>更多</span></Button></template>
      <template v-else><Button variant="ghost" class="nav-action" aria-label="唤醒手机" :disabled="!commandReady" @click="emit('command', 'device/wakeup', [])"><Power class="h-4 w-4" /><span>唤醒</span></Button><Button variant="ghost" class="nav-action" aria-label="重连画面" @click="reconnect"><RotateCw class="h-4 w-4" /><span>重连</span></Button></template>
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
.rtc-video { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); pointer-events: none; opacity: 0; }
.rtc-video.rtc-visible { opacity: 1; }
.rtc-input { opacity: 0; }
.rtc-input.detail-frame { opacity: 1; }
.phone-keyboard { position: absolute; bottom: 8px; left: 50%; width: 1px; height: 1px; padding: 0; border: 0; opacity: 0; resize: none; pointer-events: none; }
.phone-navigation { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 2px; padding: 5px 7px 0; flex-shrink: 0; }
.phone-navigation .nav-action { height: 46px; padding: 4px 0; gap: 4px; flex-direction: column; font-size: 10px; line-height: 14px; font-weight: 400; }
.phone-hint { padding: 5px 8px 9px; flex-shrink: 0; }
.phone-status { display: flex; flex-wrap: wrap; gap: 3px 12px; padding: 8px 12px; font-size: 10px; line-height: 14px; flex-shrink: 0; }
.phone-control:fullscreen { width: 100vw; height: 100dvh; max-width: none; border-radius: 0; }
.phone-control:fullscreen .phone-navigation { width: min(440px, 100%); align-self: center; }
.phone-control:fullscreen .phone-status { justify-content: center; }
.screen-action:focus-visible { outline: 2px solid #f97316; outline-offset: 2px; }
.phone-control.immersive{border:0;border-radius:0;position:relative;background:var(--background)}
.immersive .phone-heading,.immersive .phone-hint,.immersive .phone-status{display:none}
.immersive .screen-viewport{padding:0}
.immersive .phone-navigation{position:absolute;z-index:20;bottom:max(10px,env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);width:calc(100% - 20px);max-width:440px;padding:4px;border:1px solid var(--border);background:color-mix(in srgb,var(--background) 96%,transparent);backdrop-filter:blur(10px);border-radius:10px;box-shadow:0 2px 12px #00000010}
.immersive.controls-hidden .phone-navigation{display:none}
</style>
