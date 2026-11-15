<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  ChevronLeft,
  ArrowUpRight,
  BatteryCharging,
  Cable,
  Download,
  Loader2,
  Plus,
  Search,
  Trash2,
  UserRound,
  Wifi,
  MapPin,
  Smartphone,
  Package,
  Zap,
  HardDrive,
  Clock,
  Activity,
  Layers,
} from "lucide-vue-next";
import { bytesToHuman, extractArray } from "@/lib/device-detail-data";
import DevicePerformanceCharts from "@/components/devices/DevicePerformanceCharts.vue";
import { Sidebar } from "@/components/dashboard/sidebar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FeedbackTip, type FeedbackTipVariant } from "@/components/ui/feedback-tip";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Tooltip as UITooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { apiRequest, formatApiError } from "@/lib/api";
import { matchHashPath, toHashHref, useHashPathname } from "@/lib/hash-router";
import { androidVersionFromSdk } from "@/lib/android-version";

type DeviceStatus = "online" | "offline" | "busy";

type DeviceDetailApi = {
  domain?: string;
  state?: string | number;
  locked?: boolean;
  batt_charging?: boolean;
  api_available?: boolean;
  cert?: string;
  public_ip?: string;
  ip_city?: string;
  brand?: string;
  model?: string;
  sdk?: string | number;
  abi?: string;
  mem_total?: number;
  disk_total?: number;
  boot_time?: number;
  last_heartbeat_time?: number;
  version?: string;
};

type UserApi = {
  id?: number | string;
  username?: string;
  name?: string;
  contact?: string | null;
  is_admin?: boolean;
};

type ListEnvelope<T> = {
  data?: T[];
  total?: number;
};

type DeviceInfo = {
  id: string;
  status: DeviceStatus;
  publicIp: string;
  region: string;
  brand: string;
  model: string;
  osVersion: string;
  architecture: string;
  memory: string;
  disk: string;
  uptimeText: string;
  heartbeatText: string;
  isCharging: boolean;
  isApiAvailable: boolean;
  cert: string;
};

function normalizeStatus(state: string | number | undefined, locked: boolean): DeviceStatus {
  if (state === 1 || state === "online") return locked ? "busy" : "online";
  return "offline";
}

function relativeTime(seconds?: number) {
  const ts = Number(seconds || 0);
  if (!ts) return "-";
  const diff = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  const d = Math.floor(diff / 86400);
  const h = Math.floor((diff % 86400) / 3600);
  const m = Math.floor((diff % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

const statusDotColors: Record<DeviceStatus, string> = {
  online: "bg-[var(--status-online)]",
  busy: "bg-[var(--status-busy)]",
  offline: "bg-[var(--status-offline)]",
};

const statusTextColors: Record<DeviceStatus, string> = {
  online: "text-[var(--status-online)]",
  busy: "text-[var(--status-busy)]",
  offline: "text-[var(--status-offline)]",
};

const hashPathname = useHashPathname();
const { t } = useTranslation();
const hashDeviceId = computed(() => matchHashPath(hashPathname.value, /^\/devices\/([^/]+)$/));
const deviceId = computed(() => hashDeviceId.value);

const loadError = ref<string | null>(null);
const device = ref<DeviceInfo | null>(null);
const users = ref<UserApi[]>([]);
const usersPickerOpen = ref(false);
const candidateUsers = ref<UserApi[]>([]);
const candidateUsersLoading = ref(false);
const userSearch = ref("");
const userActionBusy = ref(false);
const removingUserId = ref<string | number | null>(null);
const userTip = ref<{ id: number; text: string; variant: FeedbackTipVariant } | null>(null);
watch(deviceId, (_value, _oldValue, onCleanup) => {
  if (!deviceId.value) {
    return;
  }
  let cancelled = false;

  const load = async () => {
    loadError.value = null;
    const id = deviceId.value;
    if (!id) return;
    try {
      const [deviceResp, usersResp] = await Promise.all([
        apiRequest<DeviceDetailApi>(`/api/v1/device/${encodeURIComponent(id)}`, {
          method: "GET",
          errorMessage: "Failed to load device",
        }),
        apiRequest<ListEnvelope<UserApi>>(`/api/v1/device/${encodeURIComponent(id)}/alloc?page=1&size=200`, {
          method: "GET",
          errorMessage: "Failed to load device users",
        }),
      ]);

      if (cancelled) return;

      const raw = (deviceResp.data as unknown as DeviceDetailApi) || {};
      const status = normalizeStatus(raw.state, Boolean(raw.locked));

      device.value = {
        id: String(raw.domain || id),
        status,
        publicIp: String(raw.public_ip || "-"),
        region: String(raw.ip_city || "-"),
        brand: String(raw.brand || "-"),
        model: String(raw.model || "-"),
        osVersion: androidVersionFromSdk(raw.sdk),
        architecture: String(raw.abi || "-"),
        memory: bytesToHuman(raw.mem_total),
        disk: bytesToHuman(raw.disk_total),
        uptimeText: relativeTime(raw.boot_time),
        heartbeatText: relativeTime(raw.last_heartbeat_time),
        isCharging: Boolean(raw.batt_charging),
        isApiAvailable: Boolean(raw.api_available),
        cert: String(raw.cert || ""),
      };

      users.value = extractArray<UserApi>(usersResp.data);

    } catch (err) {
      if (!cancelled) loadError.value = formatApiError(err, "Failed to load device details");
    }
  };

  load();
  onCleanup(() => {
    cancelled = true;
  });
}, { immediate: true });

watch([usersPickerOpen, userSearch, users, deviceId], (_values, _oldValues, onCleanup) => {
  if (!usersPickerOpen.value || !deviceId.value) return;
  let cancelled = false;
  const loadCandidates = async () => {
    candidateUsersLoading.value = true;
    try {
      const qs = new URLSearchParams({
        page: "1",
        size: "20",
        sort: "id",
        order: "desc",
      });
      const keyword = userSearch.value.trim();
      if (keyword) {
        qs.set("filter", JSON.stringify([{ field: "name", op: "like", value: keyword }]));
      }
      const resp = await apiRequest<ListEnvelope<UserApi>>(`/api/v1/user?${qs.toString()}`, {
        method: "GET",
        errorMessage: "Failed to load user list",
      });
      if (cancelled) return;
      const allocated = new Set(users.value.map((u) => String(u.id ?? "")));
      const list = extractArray<UserApi>(resp.data).filter((u) => {
        const uid = String(u?.id ?? "");
        return Boolean(uid) && !allocated.has(uid) && !Boolean(u?.is_admin);
      });
      candidateUsers.value = list;
    } catch {
      if (!cancelled) candidateUsers.value = [];
    } finally {
      if (!cancelled) candidateUsersLoading.value = false;
    }
  };
  void loadCandidates();
  onCleanup(() => {
    cancelled = true;
  });
});

const d = computed<DeviceInfo>(
  () =>
    device.value || {
      id: deviceId.value || "-",
      status: "offline" as DeviceStatus,
      publicIp: "-",
      region: "-",
      brand: "-",
      model: "-",
      osVersion: "-",
      architecture: "-",
      memory: "-",
      disk: "-",
      uptimeText: "-",
      heartbeatText: "-",
      isCharging: false,
      isApiAvailable: false,
      cert: "",
    },
);

const onDownloadCert = () => {
  if (!d.value.cert) return;
  const blob = new Blob([d.value.cert], { type: "application/x-pem-file" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${d.value.id}.pem`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

const showUserTip = (text: string, variant: FeedbackTipVariant = "error") => {
  userTip.value = { id: Date.now() + Math.random(), text, variant };
};

const reloadAssignedUsers = async () => {
  if (!deviceId.value) return;
  const resp = await apiRequest<ListEnvelope<UserApi>>(`/api/v1/device/${encodeURIComponent(deviceId.value)}/alloc?page=1&size=200`, {
    method: "GET",
    errorMessage: "Failed to load device users",
  });
  users.value = extractArray<UserApi>(resp.data);
};

const onAddUser = async (uid: string | number) => {
  if (!deviceId.value || !uid) return;
  userActionBusy.value = true;
  try {
    await apiRequest(`/api/v1/device/${encodeURIComponent(deviceId.value)}/alloc`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ id: String(uid) }).toString(),
    });
    await reloadAssignedUsers();
    usersPickerOpen.value = false;
    userSearch.value = "";
    showUserTip(t.value.deviceDetail.addUserSuccess, "success");
  } catch (err) {
    showUserTip(formatApiError(err, t.value.deviceDetail.addUserFailed), "error");
  } finally {
    userActionBusy.value = false;
  }
};

const onRemoveUser = async (uid: string | number) => {
  if (!deviceId.value || !uid) return;
  removingUserId.value = uid;
  try {
    await apiRequest(`/api/v1/device/${encodeURIComponent(deviceId.value)}/alloc?id=${encodeURIComponent(String(uid))}`, {
      method: "DELETE",
    });
    await reloadAssignedUsers();
    showUserTip(t.value.deviceDetail.removeUserSuccess, "success");
  } catch (err) {
    showUserTip(formatApiError(err, t.value.deviceDetail.removeUserFailed), "error");
  } finally {
    removingUserId.value = null;
  }
};
</script>

<template>
  <div class="flex h-screen bg-[#f5f5f7] dark:bg-background">
    <Sidebar />

    <main class="flex min-w-0 flex-1 flex-col overflow-hidden lg:ml-[220px]">
      <div class="flex h-14 shrink-0 items-center border-b border-gray-100 bg-white px-5 dark:border-border dark:bg-background">
        <div class="flex w-full min-w-0 items-center justify-between gap-3">
          <div class="flex min-w-0 items-center gap-2">
            <Button as-child size="icon" variant="ghost" class="h-7 w-7 shrink-0">
              <a :href="toHashHref('/devices')">
                <ChevronLeft class="h-3.5 w-3.5" />
              </a>
            </Button>
            <div>
              <p class="text-xs text-muted-foreground">{{ t.deviceDetail.title }}</p>
              <p class="truncate text-base font-semibold leading-tight">{{ d.id }}</p>
            </div>
            <div class="ml-2 flex flex-wrap items-center gap-2">
              <a :href="toHashHref('/evidence/' + deviceId)" class="rounded border px-3 py-1.5 text-sm">查看任务证据</a>
              <a :href="toHashHref('/ai/' + deviceId)" class="rounded border px-3 py-1.5 text-sm">AI 任务</a>
            </div>
          </div>
          <span :class="cn('inline-flex items-center gap-1.5 font-medium', statusTextColors[d.status])">
            <span :class="cn('inline-block h-2 w-2 rounded-full', statusDotColors[d.status])" />
            {{ t.deviceDetail.statusText[d.status] }}
          </span>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto p-3">
        <div class="flex h-full min-h-0 gap-3">
          <aside class="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-card p-3">
            <div>
              <div class="flex items-center justify-between">
                <h2 class="text-sm font-semibold text-foreground">{{ t.deviceDetail.deviceInfo }}</h2>
                <div class="flex items-center gap-1">
                  <UITooltip :delay-duration="100">
                    <TooltipTrigger as-child>
                      <Button variant="ghost" size="icon" class="relative h-7 w-7 rounded text-muted-foreground hover:bg-muted/50 hover:text-foreground">
                        <BatteryCharging :class="cn('h-4 w-4', d.isCharging ? 'text-blue-500 animate-pulse' : 'text-slate-400')" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" class="text-xs">
                      {{ d.isCharging ? t.deviceDetail.actions.chargingActive : t.deviceDetail.actions.notCharging }}
                    </TooltipContent>
                  </UITooltip>
                  <UITooltip :delay-duration="100">
                    <TooltipTrigger as-child>
                      <Button variant="ghost" size="icon" class="relative h-7 w-7 rounded text-muted-foreground hover:bg-muted/50 hover:text-foreground">
                        <Cable :class="cn('h-4 w-4', d.isApiAvailable ? 'text-emerald-500' : 'text-red-500')" />
                        <span :class="cn('absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full', d.isApiAvailable ? 'bg-emerald-500' : 'bg-red-500')" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" class="text-xs">
                      {{ d.isApiAvailable ? t.deviceDetail.actions.apiAvailable : t.deviceDetail.actions.apiUnavailable }}
                    </TooltipContent>
                  </UITooltip>
                  <UITooltip :delay-duration="100">
                    <TooltipTrigger as-child>
                      <Button variant="ghost" size="icon" class="h-7 w-7 rounded text-muted-foreground hover:bg-muted/50 hover:text-foreground" @click="onDownloadCert">
                        <Download class="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" class="text-xs">{{ t.deviceDetail.actions.downloadCert }}</TooltipContent>
                  </UITooltip>
                  <UITooltip :delay-duration="100">
                    <TooltipTrigger as-child>
                      <Button as-child variant="ghost" size="sm" class="h-7 gap-1 rounded px-2 text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground">
                        <a :href="`/d/${encodeURIComponent(d.id)}/`" target="_blank" rel="noreferrer">
                          {{ t.deviceDetail.actions.gotoRemote }}
                          <ArrowUpRight class="h-3.5 w-3.5" />
                        </a>
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" class="text-xs">{{ t.deviceDetail.actions.gotoRemote }}</TooltipContent>
                  </UITooltip>
                </div>
              </div>
            </div>

            <Card class="flex flex-1 flex-col rounded-lg border border-border bg-card p-3 shadow-none">
              <CardHeader class="shrink-0 p-0">
                <CardTitle class="inline-flex items-center gap-1 text-xs font-semibold">
                  <Smartphone class="h-3.5 w-3.5 text-muted-foreground" />
                  {{ t.deviceDetail.deviceInfo }}
                </CardTitle>
              </CardHeader>
              <CardContent class="flex-1 overflow-y-auto p-0 pt-2">
                <div class="space-y-1.5 text-[11px]">
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Wifi class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.publicIp }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', 'font-mono')">{{ d.publicIp || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><MapPin class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.region }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', '')">{{ d.region || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Smartphone class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.brand }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', '')">{{ d.brand || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Smartphone class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.model }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', '')">{{ d.model || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Layers class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.osVersion }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', '')">{{ d.osVersion || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Package class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.architecture }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', 'font-mono')">{{ d.architecture || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Zap class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.memory }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', 'font-mono')">{{ d.memory || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><HardDrive class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.disk }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', 'font-mono')">{{ d.disk || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Clock class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.uptime }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', '')">{{ d.uptimeText || "-" }}</p>
                  </div>
                  <div class="grid grid-cols-[88px_minmax(0,1fr)] items-start gap-2">
                    <p class="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <span class="text-muted-foreground"><Activity class="h-3 w-3" /></span>
                      {{ t.deviceDetail.fields.lastHeartbeat }}
                    </p>
                    <p :class="cn('truncate text-[11px] font-medium', '')">{{ d.heartbeatText || "-" }}</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <p class="inline-flex items-center gap-1 text-xs font-semibold">
                  <UserRound class="h-3.5 w-3.5 text-muted-foreground" />
                  {{ t.deviceDetail.assignedUsers }}
                </p>
                <Popover :open="usersPickerOpen" @update:open="usersPickerOpen = $event">
                  <PopoverTrigger as-child>
                    <Button variant="ghost" size="icon" class="h-6 w-6">
                      <Plus class="h-3.5 w-3.5" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent align="end" class="w-[260px] p-0">
                    <div class="p-2">
                      <div class="relative">
                        <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                        <Input
                          class="h-8 pl-8 text-xs"
                          :placeholder="t.deviceDetail.searchUserPlaceholder"
                          v-model="userSearch"
                        />
                      </div>
                    </div>
                    <div class="border-t border-border" />
                    <div class="max-h-[220px] overflow-y-auto py-1">
                      <div v-if="candidateUsersLoading" class="flex items-center justify-center gap-2 px-3 py-8 text-xs text-muted-foreground">
                        <Loader2 class="h-3.5 w-3.5 animate-spin" />
                        {{ t.common.loading }}
                      </div>
                      <p v-else-if="candidateUsers.length === 0" class="px-3 py-8 text-center text-xs text-muted-foreground">{{ t.deviceDetail.noCandidateUsers }}</p>
                      <button
                        v-else
                        v-for="(u, idx) in candidateUsers"
                        :key="`${u.id || 'c'}-${idx}`"
                        type="button"
                        :disabled="userActionBusy"
                        @click="void onAddUser(String(u.id || ''))"
                        class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-muted/50"
                      >
                        <span class="h-2.5 w-2.5 shrink-0 rounded-sm bg-blue-500/80" />
                        <span class="flex-1 truncate">{{ u.name || u.username || "-" }}</span>
                      </button>
                    </div>
                  </PopoverContent>
                </Popover>
              </div>
              <FeedbackTip v-if="userTip" :key="userTip.id" :toast-id="userTip.id" :message="userTip.text" :variant="userTip.variant" class="mb-1" />
              <div class="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
                <p v-if="users.length === 0" class="px-2 py-3 text-center text-xs text-muted-foreground">{{ t.deviceDetail.noUsers }}</p>
                <div v-else v-for="(u, idx) in users" :key="`${u.id || 'u'}-${idx}`" class="flex items-center gap-2 rounded-md border border-border px-2 py-2">
                  <span class="inline-flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground">
                    <UserRound class="h-3.5 w-3.5" />
                  </span>
                  <span class="min-w-0 flex-1 truncate text-xs font-medium text-foreground">{{ u.name || u.username || "-" }}</span>
                  <Button
                    variant="ghost"
                    size="icon"
                    class="h-6 w-6 text-destructive hover:text-destructive"
                    :disabled="removingUserId === u.id"
                    @click="void onRemoveUser(String(u.id || ''))"
                  >
                    <Loader2 v-if="removingUserId === u.id" class="h-3.5 w-3.5 animate-spin" />
                    <Trash2 v-else class="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          </aside>

          <div class="min-w-0 flex-1 overflow-auto">
            <FeedbackTip v-if="loadError" :message="loadError" variant="error" :compact="false" />
            <DevicePerformanceCharts v-if="deviceId" :key="deviceId" :device-id="deviceId" class="min-h-full" />
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
