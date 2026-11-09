<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ArrowLeft, Circle, Square, RotateCw, Power, Pause, Play } from 'lucide-vue-next';
import { deviceSocket } from '@/lib/device-channel';
import { Button } from '@/components/ui/button';

const props = defineProps<{ deviceId: string; online: boolean; commandReady: boolean; rotation?: number }>();
const emit = defineEmits<{ command: [command: string, data: unknown]; log: [message: string] }>();
const canvas = ref<HTMLCanvasElement>();
const state = ref('connecting');
const touchReady = ref(false);
const frames = ref(0);
const fps = ref(0);
const paused = ref(false);
const visible = ref(!document.hidden);
const mode = ref<'h264' | 'mjpeg'>('h264');
const screenSize = ref({ width: 540, height: 1194 });
const enabled = computed(() => props.online && visible.value && !paused.value);
let dispose: (() => void) | undefined;
let retry: ReturnType<typeof setTimeout> | undefined;
let touch: WebSocket | undefined;
let dimensions = { width: 0, height: 0 };
let pointer: number | null = null;
let failures = 0;

function release() {
  if (pointer !== null && touch?.readyState === WebSocket.OPEN) touch.send(JSON.stringify({ action: 'up', id: 0 }));
  pointer = null;
}
function stop() { release(); clearTimeout(retry); dispose?.(); dispose = undefined; touchReady.value = false; fps.value = 0; }
function start() {
  stop();
  if (!enabled.value || !canvas.value) { state.value = 'paused'; return; }
  state.value = 'connecting';
  const surface = canvas.value;
  const context = surface.getContext('2d', { alpha: false });
  if (!context) return;
  let closed = false;
  let ws: WebSocket | undefined;
  let worker: Worker | undefined;
  let workerUrl: string | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;
  let lastFrame = Date.now(), count = 0, measured = Date.now(), pending = 0;
  let decoding = false, latest: Blob | null = null;
  const touchWs = new WebSocket(deviceSocket(props.deviceId, '/ws/screen/touch'));
  touch = touchWs;
  dispose = () => {
    closed = true; latest = null; clearInterval(timer); worker?.terminate(); if (workerUrl) URL.revokeObjectURL(workerUrl);
    for (const s of [ws, touchWs]) if (s) { s.onmessage = s.onopen = s.onclose = s.onerror = null; s.close(); }
    touch = undefined;
  };
  function recover() {
    if (closed) return;
    stop(); state.value = 'reconnecting';
    if (!enabled.value) return;
    if (++failures >= 2 && mode.value === 'h264') { mode.value = 'mjpeg'; emit('log', '视频编码不可用，已切换兼容画面'); }
    retry = setTimeout(start, Math.min(15000, 1500 * 2 ** Math.min(failures, 3)));
  }
  function drawn(width: number, height: number) {
    screenSize.value = { width, height }; state.value = 'live'; lastFrame = Date.now(); frames.value++; count++; failures = 0;
  }
  function size(width: number, height: number) { if (surface.width !== width || surface.height !== height) { surface.width = width; surface.height = height; } }
  touchWs.onopen = () => touchWs.send(JSON.stringify({ action: 'reset' }));
  touchWs.onmessage = e => { try { const m = JSON.parse(e.data); if (m.type === 'OK' && m.w > 0 && m.h > 0) { dimensions = { width: m.w, height: m.h }; touchReady.value = true; } } catch {} };
  touchWs.onclose = recover; touchWs.onerror = recover;
  try {
    if (mode.value === 'h264') {
      const decoder = new URL(`/d/${encodeURIComponent(props.deviceId)}/static/js/decoder.js`, location.href).href;
      const code = `var window=self;self.window=self;var document=undefined;importScripts(${JSON.stringify(decoder)});var decoder=new H264Decoder();onmessage=function(e){try{var b=new Uint8Array(e.data);if(decoder.decode(b)===H264Decoder.PIC_RDY){var w=decoder.width,h=decoder.height,yuv=decoder.pic,out=new Uint8ClampedArray(w*h*4),uw=w>>1,uo=w*h,vo=uo+uw*(h>>1);for(var j=0,p=0;j<h;j++)for(var i=0;i<w;i++){var y=yuv[j*w+i],uv=(j>>1)*uw+(i>>1),u=yuv[uo+uv]-128,v=yuv[vo+uv]-128;out[p++]=y+1.402*v;out[p++]=y-.344136*u-.714136*v;out[p++]=y+1.772*u;out[p++]=255;}postMessage({w:w,h:h,rgba:out.buffer},[out.buffer]);}postMessage({done:true});}catch(e){postMessage({error:true});}};`;
      workerUrl = URL.createObjectURL(new Blob([code], { type: 'application/javascript' }));
      worker = new Worker(workerUrl);
      worker.onerror = recover;
      worker.onmessage = e => {
        if (closed) return;
        const d = e.data;
        if (d.error) { recover(); return; }
        if (d.done) { pending = Math.max(0, pending - 1); return; }
        size(d.w, d.h); context.putImageData(new ImageData(new Uint8ClampedArray(d.rgba), d.w, d.h), 0, 0); drawn(d.w, d.h);
      };
    }
    ws = new WebSocket(deviceSocket(props.deviceId, `/ws/screen/25@${mode.value === 'h264' ? 15 : 8}/live?type=${mode.value}&backend=${mode.value === 'h264' ? 1 : 0}&scale=0.5&display=0`));
    ws.binaryType = mode.value === 'h264' ? 'arraybuffer' : 'blob';
    ws.onmessage = async e => {
      if (closed) return;
      if (worker && e.data instanceof ArrayBuffer) {
        // H.264 frames depend on one another: restart instead of dropping packets or accumulating latency.
        if (++pending > 8) { recover(); return; }
        worker.postMessage(e.data, [e.data]); return;
      }
      if (!(e.data instanceof Blob)) return;
      latest = e.data;
      if (decoding) return;
      decoding = true;
      try {
        while (latest && !closed) {
          const b = latest; latest = null;
          const image = await createImageBitmap(b);
          try { if (!closed) { size(image.width, image.height); context.drawImage(image, 0, 0); drawn(image.width, image.height); } } finally { image.close(); }
        }
      } catch { recover(); } finally { decoding = false; }
    };
    ws.onclose = recover; ws.onerror = recover;
    timer = setInterval(() => {
      const now = Date.now(); fps.value = Math.round(count * 1000 / (now - measured)); count = 0; measured = now;
      if (now - lastFrame > 15000) recover();
    }, 2000);
  } catch { recover(); }
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
function move(e: PointerEvent) { if (pointer === e.pointerId) send('move', e); }
function up(e: PointerEvent) { if (pointer !== e.pointerId) return; release(); if (canvas.value?.hasPointerCapture(e.pointerId)) canvas.value.releasePointerCapture(e.pointerId); }
function key(code: number) { emit('command', 'device/input/keycode', [0, code]); }
function visibility() { visible.value = !document.hidden; }
watch(enabled, start);
watch(() => props.rotation, start);
onMounted(() => { document.addEventListener('visibilitychange', visibility); start(); });
onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', visibility); });
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-border bg-background">
    <div class="flex items-center justify-between gap-2 border-b px-3 py-2 text-xs">
      <span class="font-medium">实时画面</span><span class="text-muted-foreground">{{ state === 'live' ? `${fps} FPS · ${mode === 'h264' ? 'H.264' : '兼容模式'}` : '等待画面' }}</span>
      <button :title="paused ? '恢复画面' : '暂停画面'" :aria-label="paused ? '恢复画面' : '暂停画面'" @click="paused = !paused"><component :is="paused ? Play : Pause" class="h-3.5 w-3.5" /></button>
    </div>
    <div class="relative flex min-h-[260px] items-center justify-center bg-black p-2" :data-control-state="state" :data-control-frames="frames">
      <canvas ref="canvas" :style="{ aspectRatio: `${screenSize.width}/${screenSize.height}`, maxHeight: 'min(68vh, 680px)', width: 'auto', maxWidth: '100%' }" class="block touch-none select-none" aria-label="手机实时远控画面" @pointerdown.prevent="down" @pointermove.prevent="move" @pointerup="up" @pointercancel="up" @lostpointercapture="release" @contextmenu.prevent />
      <div v-if="state !== 'live'" class="absolute inset-0 flex items-center justify-center bg-black/75 px-4 text-center text-sm text-white">{{ !online ? '设备离线，等待重新连接' : paused || !visible ? '画面已暂停' : state === 'reconnecting' ? '画面重连中…' : '正在连接手机画面…' }}</div>
    </div>
    <div class="flex items-center justify-around border-t p-2">
      <Button variant="ghost" size="icon" title="返回" aria-label="返回手机上一页" :disabled="!commandReady" @click="key(4)"><ArrowLeft class="h-4 w-4" /></Button>
      <Button variant="ghost" size="icon" title="主页" aria-label="手机主页" :disabled="!commandReady" @click="key(3)"><Circle class="h-4 w-4" /></Button>
      <Button variant="ghost" size="icon" title="最近任务" aria-label="手机最近任务" :disabled="!commandReady" @click="key(187)"><Square class="h-4 w-4" /></Button>
      <Button variant="ghost" size="icon" title="唤醒" aria-label="唤醒手机" :disabled="!commandReady" @click="emit('command', 'device/wakeup', [])"><Power class="h-4 w-4" /></Button>
      <Button variant="ghost" size="icon" title="重连画面" aria-label="重连画面" @click="failures = 0; mode = 'h264'; start()"><RotateCw class="h-4 w-4" /></Button>
    </div>
    <p class="border-t px-3 py-2 text-center text-[11px] text-muted-foreground">{{ touchReady ? '可直接点击、拖动手机画面' : '触控通道尚未连接' }}</p>
  </div>
</template>
