<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Loader2, MonitorOff, Pause, WifiOff } from "lucide-vue-next";
import { useTranslation } from "@/lib/i18n";
import { fitDisplay } from '@/lib/mobile-layout';

const props = defineProps<{ deviceId: string; online: boolean; paused: boolean; pending?: boolean }>();
const { t } = useTranslation();
const host = ref<HTMLDivElement | null>(null);
const canvas = ref<HTMLCanvasElement | null>(null);
const visible = ref(false);
const pageVisible = ref(!document.hidden);
const state = ref<"idle" | "connecting" | "live" | "reconnecting">("idle");
const frames = ref(0);
const hasFrame = ref(false);
const frameSize = ref({ width: 540, height: 1194 });
const boxSize = ref({ width: 200, height: 400 });
const canvasStyle = computed(() => {
  const size = fitDisplay(frameSize.value.width, frameSize.value.height, boxSize.value.width, boxSize.value.height);
  return { width: `${size.width}px`, height: `${size.height}px` };
});
let resize: ResizeObserver | undefined;
let observer: IntersectionObserver | undefined;
let cleanup: (() => void) | undefined;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let attempts = 0;

const enabled = computed(() => props.online && !props.paused && visible.value && pageVisible.value);
const message = computed(() => {
  if (!props.online) return props.pending ? t.value.devices.pendingDeviceWaiting : t.value.devices.wall.offline;
  if (props.paused || !pageVisible.value || !visible.value) return t.value.devices.wall.paused;
  if (state.value === "reconnecting") return t.value.devices.wall.reconnecting;
  return t.value.devices.wall.connecting;
});

function stop() {
  clearTimeout(retryTimer);
  retryTimer = undefined;
  cleanup?.();
  cleanup = undefined;
}

function connect() {
  stop();
  if (!enabled.value || !canvas.value) return;
  const surface = canvas.value;
  const context = surface.getContext("2d", { alpha: false });
  if (!context) return;
  state.value = attempts ? "reconnecting" : "connecting";
  const url = new URL(`/d/${encodeURIComponent(props.deviceId)}/ws/screen/25@5/live`, window.location.href);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  url.search = "scale=0.3&type=mjpeg&backend=0&display=0";

  let closed = false;
  let socket: WebSocket | undefined;
  let latest: Blob | null = null;
  let decoding = false;
  let decodeErrors = 0;
  let lastFrame = Date.now();
  let watchdog: ReturnType<typeof setInterval> | undefined;

  cleanup = () => {
    closed = true;
    latest = null;
    clearInterval(watchdog);
    if (socket) {
      socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null;
      socket.close();
    }
  };

  const reconnect = () => {
    if (closed) return;
    stop();
    if (!enabled.value) return;
    state.value = "reconnecting";
    // Do not reconnect every card in a fleet at the same instant.
    const delay = Math.min(30000, 2000 * 2 ** Math.min(attempts++, 4));
    retryTimer = setTimeout(connect, delay + Math.random() * 500);
  };

  const drawLatest = async () => {
    if (decoding) return;
    decoding = true;
    try {
      while (!closed && latest) {
        const next = latest;
        latest = null;
        let image: ImageBitmap | undefined;
        try {
          image = await createImageBitmap(next);
          if (closed) break;
          if (surface.width !== image.width || surface.height !== image.height) {
            surface.width = image.width;
            surface.height = image.height;
            frameSize.value = { width: image.width, height: image.height };
          }
          context.drawImage(image, 0, 0);
          frames.value++;
          hasFrame.value = true;
          lastFrame = Date.now();
          state.value = "live";
          attempts = 0;
          decodeErrors = 0;
        } catch {
          if (!closed && ++decodeErrors >= 3) reconnect();
        } finally {
          image?.close();
        }
      }
    } finally {
      decoding = false;
    }
  };

  try {
    socket = new WebSocket(url);
    socket.binaryType = "blob";
    socket.onmessage = (event) => {
      if (closed || !(event.data instanceof Blob)) return;
      // At most one decoding frame and one pending frame: never queue old video.
      latest = event.data;
      void drawLatest();
    };
    socket.onclose = reconnect;
    socket.onerror = reconnect;
    watchdog = setInterval(() => {
      if (Date.now() - lastFrame > 20000) reconnect();
    }, 5000);
  } catch {
    reconnect();
  }
}

function reconcile() {
  stop();
  attempts = 0;
  if (enabled.value) connect();
  else state.value = "idle";
}

function onVisibilityChange() { pageVisible.value = !document.hidden; }
watch(enabled, reconcile);
watch(() => props.deviceId, () => {
  hasFrame.value = false;
  frames.value = 0;
  reconcile();
});
watch(() => props.online, (online) => { if (!online) hasFrame.value = false; });

onMounted(() => {
  resize = new ResizeObserver(([entry]) => { if (entry) boxSize.value = { width: entry.contentRect.width, height: entry.contentRect.height }; });
  if (host.value) resize.observe(host.value);
  observer = new IntersectionObserver(([entry]) => { visible.value = entry.isIntersecting; }, { threshold: 0.01 });
  if (host.value) observer.observe(host.value);
  document.addEventListener("visibilitychange", onVisibilityChange);
});
onBeforeUnmount(() => {
  resize?.disconnect();
  observer?.disconnect();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  stop();
});
</script>

<template>
  <div ref="host" class="relative flex h-full w-full items-center justify-center overflow-hidden bg-background"
    :data-preview-state="state" :data-preview-frames="frames" :data-device-id="deviceId">
    <canvas ref="canvas" width="324" height="576" :aria-label="t.devices.wall.screen"
      :style="canvasStyle" class="block" :class="{ 'invisible': !hasFrame, 'opacity-25': state !== 'live' }" />
    <div v-if="state !== 'live'" class="absolute inset-0 flex flex-col items-center justify-center gap-2 px-3 text-center text-xs text-muted-foreground">
      <MonitorOff v-if="!online" class="h-6 w-6 text-muted-foreground" />
      <Pause v-else-if="paused || !pageVisible || !visible" class="h-6 w-6 text-muted-foreground" />
      <WifiOff v-else-if="state === 'reconnecting'" class="h-6 w-6 text-muted-foreground" />
      <Loader2 v-else class="h-5 w-5 animate-spin motion-reduce:animate-none" />
      <span>{{ message }}</span>
      <span v-if="state === 'reconnecting'" class="text-[10px] text-muted-foreground">{{ t.devices.wall.openToCheck }}</span>
    </div>
    <span v-else class="pointer-events-none absolute bottom-2 left-2 rounded bg-black/65 px-1.5 py-0.5 text-[10px] text-white">
      {{ t.devices.wall.live }}
    </span>
  </div>
</template>
