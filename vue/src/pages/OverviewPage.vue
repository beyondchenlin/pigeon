<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Check, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Search, X } from "lucide-vue-next";
import { Sidebar } from "@/components/dashboard/sidebar";
import DevicePreview from "@/components/dashboard/DevicePreview.vue";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { FeedbackTip } from "@/components/ui/feedback-tip";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import { apiRequest, formatApiError } from "@/lib/api";

interface Device {
  id: string;
  model: string;
  brand: string;
  status: "pending" | "online" | "offline";
}

type DeviceApiItem = {
  domain?: string;
  state?: string | number;
  locked?: boolean;
  model?: string;
  brand?: string;
};

type ListEnvelope<T> = {
  data?: T[];
  total?: number;
};

type GroupApiItem = {
  id: number;
  name: string;
  total?: number;
  color?: string;
};

type GroupOption = {
  id: string;
  name: string;
  total: number;
  color: string;
};

type OverviewSortField =
  | "batt_percent"
  | "comment"
  | "boot_time"
  | "model"
  | "domain"
  | "last_heartbeat_time"
  | "register_time"
  | "sdk"
  | "state";

const GROUP_COLOR_POOL = ["#22c55e", "#3b82f6", "#a855f7", "#f59e0b", "#14b8a6", "#ef4444"];

function normalizeStatus(state: string | number | undefined, _locked: boolean): Device["status"] {
  if (state === "pending") return "pending";
  if (state === 1 || state === "online") return "online";
  return "offline";
}

const { t } = useTranslation();
const currentPage = ref(1);
const selectedGroupId = ref<"all" | string>("all");
const groupOpen = ref(false);
const groupSearch = ref("");
const groups = ref<GroupOption[]>([]);
const groupLoading = ref(false);
const loading = ref(false);
const loadError = ref<string | null>(null);
const devices = ref<Device[]>([]);
const total = ref(0);
const pageSize = ref(30);
const sortField = ref<OverviewSortField>("state");
const sortOrder = ref<"asc" | "desc">("asc");
let groupReqId = 0;
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));

watch(
  [currentPage, selectedGroupId, pageSize, sortField, sortOrder, () => t.value.overviewPage.loadDevicesFailed],
  (_value, _oldValue, onCleanup) => {
    let cancelled = false;
    const load = async () => {
      loading.value = true;
      loadError.value = null;
      try {
        const basePath =
          selectedGroupId.value === "all" ? "/api/v1/device" : `/api/v1/group/${selectedGroupId.value}/devices`;
        const params = new URLSearchParams();
        params.set("page", String(currentPage.value));
        params.set("size", String(pageSize.value));
        params.set("sort", sortField.value);
        params.set("order", sortOrder.value);
        const resp = await apiRequest<ListEnvelope<DeviceApiItem>>(`${basePath}?${params.toString()}`, {
          method: "GET",
          errorMessage: t.value.overviewPage.loadDevicesFailed,
        });
        if (cancelled) return;
        const list = Array.isArray(resp.data?.data) ? resp.data.data : [];
        const nextTotal = Number(resp.data?.total || 0);
        const remoteTotalPages = Math.max(1, Math.ceil(nextTotal / pageSize.value));
        if (currentPage.value > remoteTotalPages) {
          currentPage.value = remoteTotalPages;
          return;
        }
        devices.value = list
          .map((d) => ({
            id: String(d.domain || ""),
            model: String(d.model || "-"),
            brand: String(d.brand || "-"),
            status: normalizeStatus(d.state, Boolean(d.locked)),
          }))
          .filter((d) => d.id);
        total.value = nextTotal;
      } catch (err) {
        if (!cancelled) {
          devices.value = [];
          total.value = 0;
          loadError.value = formatApiError(err, t.value.overviewPage.loadDevicesFailed);
        }
      } finally {
        if (!cancelled) loading.value = false;
      }
    };
    void load();
    onCleanup(() => {
      cancelled = true;
    });
  },
  { immediate: true },
);

watch([groupOpen, groupSearch], (_value, _oldValue, onCleanup) => {
  if (!groupOpen.value) return;
  const timer = window.setTimeout(async () => {
    const reqId = ++groupReqId;
    groupLoading.value = true;
    try {
      const params = new URLSearchParams();
      params.set("page", "1");
      params.set("size", "100");
      params.set("sort", "order");
      params.set("order", "asc");
      const q = groupSearch.value.trim();
      if (q) {
        params.set("filter", JSON.stringify([{ field: "name", op: "like", value: q }]));
      }
      const resp = await apiRequest<ListEnvelope<GroupApiItem>>(`/api/v1/group?${params.toString()}`, {
        method: "GET",
        errorMessage: t.value.overviewPage.loadGroupsFailed,
      });
      if (reqId !== groupReqId) return;
      const list = Array.isArray(resp.data?.data) ? resp.data.data : [];
      groups.value = list.map((g, idx) => ({
        id: String(g.id),
        name: String(g.name || ""),
        total: Number(g.total || 0),
        color: g.color || GROUP_COLOR_POOL[idx % GROUP_COLOR_POOL.length],
      }));
    } catch {
      if (reqId === groupReqId) groups.value = [];
    } finally {
      if (reqId === groupReqId) groupLoading.value = false;
    }
  }, 180);
  onCleanup(() => window.clearTimeout(timer));
});

const getStatusDot = (status: Device["status"]) => {
  switch (status) {
    case "online":
      return "bg-green-500";
    case "pending":
      return "bg-yellow-500";
    case "offline":
      return "bg-gray-500";
  }
};

const selectedGroup = computed(() =>
  selectedGroupId.value === "all" ? null : groups.value.find((g) => g.id === selectedGroupId.value),
);
const groupButtonLabel = computed(() =>
  selectedGroup.value
    ? `${selectedGroup.value.name} (${selectedGroup.value.total})`
    : `${t.value.devices.allDevices} (${total.value})`,
);

const sortBarWidths = computed(() =>
  sortOrder.value === "asc" ? ["w-2.5", "w-4", "w-5.5"] : ["w-5.5", "w-4", "w-2.5"],
);

const onPageSizeChange = (value: string) => {
  pageSize.value = Number(value);
  currentPage.value = 1;
};

const onSortFieldChange = (value: string) => {
  sortField.value = value as OverviewSortField;
  currentPage.value = 1;
};

const toggleSortOrder = () => {
  sortOrder.value = sortOrder.value === "asc" ? "desc" : "asc";
  currentPage.value = 1;
};

const onGroupOpenChange = (open: boolean) => {
  groupOpen.value = open;
  if (!open) groupSearch.value = "";
};

const selectGroup = (id: "all" | string) => {
  selectedGroupId.value = id;
  currentPage.value = 1;
  groupOpen.value = false;
};

const viewAllDevices = () => {
  selectedGroupId.value = "all";
  currentPage.value = 1;
};

const ratios = ref<Record<string, string>>({});
function setRatio(id: string, width: number, height: number) { if (width > 0 && height > 0) ratios.value[id] = `${width} / ${height}`; }

const openDevice = (device: Device) => {
  if (device.status === "online") {
    window.open(`/d/${encodeURIComponent(device.id)}/`, "_blank");
  }
};
</script>

<template>
  <div class="flex h-screen bg-[#f5f5f7]">
    <Sidebar />

    <main class="flex flex-1 flex-col overflow-hidden lg:ml-[220px]">
      <!-- Header -->
      <div class="h-14 shrink-0 border-b border-gray-100 bg-white px-5 py-2.5">
        <div class="flex w-full items-center justify-between gap-4">
          <div class="min-w-0 flex-1">
            <h1 class="text-base font-semibold">{{ t.overviewPage.title }}</h1>
            <FeedbackTip v-if="loadError" :message="loadError" variant="error" class="mt-1" />
          </div>
          <div v-if="total > 0" class="flex shrink-0 items-center gap-1 h-8">
            <Select :model-value="String(pageSize)" @update:model-value="onPageSizeChange">
              <SelectTrigger size="sm" class="h-8 w-[68px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem v-for="size in [30, 50, 70, 90]" :key="size" :value="String(size)" class="text-xs">
                  {{ size }}
                </SelectItem>
              </SelectContent>
            </Select>
            <span class="mx-1 text-xs text-muted-foreground">
              {{ t.overviewPage.page.replace("{page}", String(currentPage)) }} / {{ totalPages }}
            </span>
            <Button
              variant="outline"
              size="icon"
              class="h-8 w-8"
              :disabled="currentPage === 1"
              @click="currentPage = 1"
            >
              <ChevronsLeft class="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              class="h-8 w-8"
              :disabled="currentPage === 1"
              @click="currentPage = Math.max(1, currentPage - 1)"
            >
              <ChevronLeft class="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              class="h-8 w-8"
              :disabled="currentPage === totalPages"
              @click="currentPage = Math.min(totalPages, currentPage + 1)"
            >
              <ChevronRight class="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              class="h-8 w-8"
              :disabled="currentPage === totalPages"
              @click="currentPage = totalPages"
            >
              <ChevronsRight class="h-3.5 w-3.5" />
            </Button>
          </div>
          <Popover :open="groupOpen" @update:open="onGroupOpenChange">
            <div class="flex items-center overflow-hidden rounded-md border border-input bg-background">
              <Select :model-value="sortField" @update:model-value="onSortFieldChange">
                <SelectTrigger size="sm" class="h-8 w-[134px] rounded-none border-0 text-center text-xs shadow-none">
                  <SelectValue :placeholder="t.overviewPage.sortField" class="text-center" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="state">{{ t.overviewPage.sortState }}</SelectItem>
                  <SelectItem value="batt_percent">{{ t.overviewPage.sortBattPercent }}</SelectItem>
                  <SelectItem value="comment">{{ t.overviewPage.sortComment }}</SelectItem>
                  <SelectItem value="boot_time">{{ t.overviewPage.sortBootTime }}</SelectItem>
                  <SelectItem value="model">{{ t.overviewPage.sortModel }}</SelectItem>
                  <SelectItem value="domain">{{ t.overviewPage.sortDomain }}</SelectItem>
                  <SelectItem value="last_heartbeat_time">{{ t.overviewPage.sortLastHeartbeatTime }}</SelectItem>
                  <SelectItem value="register_time">{{ t.overviewPage.sortRegisterTime }}</SelectItem>
                  <SelectItem value="sdk">{{ t.overviewPage.sortSdk }}</SelectItem>
                </SelectContent>
              </Select>
              <Button
                variant="ghost"
                size="sm"
                class="h-8 w-8 rounded-none border-0 border-l border-input px-0"
                :title="sortOrder === 'asc' ? t.overviewPage.sortAsc : t.overviewPage.sortDesc"
                @click="toggleSortOrder"
              >
                <span class="inline-flex h-4 w-5 flex-col items-start justify-center gap-0.5">
                  <span :class="cn('block h-0.5 rounded-full bg-current', sortBarWidths[0])" />
                  <span :class="cn('block h-0.5 rounded-full bg-current', sortBarWidths[1])" />
                  <span :class="cn('block h-0.5 rounded-full bg-current', sortBarWidths[2])" />
                </span>
              </Button>
            </div>
            <PopoverTrigger as-child>
              <Button variant="outline" class="h-8 min-w-[128px] justify-between px-2.5 text-xs font-medium">
                <span class="truncate">{{ groupButtonLabel }}</span>
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" class="w-[260px] p-0">
              <div class="p-2">
                <div class="relative">
                  <Search
                    class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                  />
                  <Input
                    v-model="groupSearch"
                    :placeholder="t.devices.searchGroupPlaceholder"
                    class="h-8 pl-8 text-xs"
                  />
                </div>
              </div>
              <div class="border-t border-border" />
              <div class="max-h-[240px] overflow-y-auto py-1">
                <button
                  type="button"
                  class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/60"
                  @click="selectGroup('all')"
                >
                  <span class="flex-1 truncate">{{ t.devices.allDevices }}</span>
                  <span class="text-xs text-muted-foreground tabular-nums">({{ total }})</span>
                  <Check v-if="selectedGroupId === 'all'" class="h-3.5 w-3.5 text-foreground" />
                </button>
                <div v-if="groupLoading" class="px-4 py-8 text-center text-xs text-muted-foreground">
                  {{ t.common.loading }}
                </div>
                <div v-else-if="groups.length === 0" class="px-4 py-8 text-center text-xs text-muted-foreground">
                  {{ t.devices.noGroupFound }}
                </div>
                <template v-else>
                  <button
                    v-for="group in groups"
                    :key="group.id"
                    type="button"
                    class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/60"
                    @click="selectGroup(group.id)"
                  >
                    <span class="h-2.5 w-2.5 shrink-0 rounded-sm" :style="{ backgroundColor: group.color }" />
                    <span class="flex-1 truncate">{{ group.name }}</span>
                    <span class="text-xs text-muted-foreground tabular-nums">{{ group.total }}</span>
                    <Check v-if="selectedGroupId === group.id" class="h-3.5 w-3.5 text-foreground" />
                  </button>
                </template>
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      <!-- Content -->
      <div class="min-h-0 flex-1 overflow-y-auto p-3">
          <!-- High-Density Grid - Vertical Device Screens -->
          <div
            v-if="devices.length === 0 && !loading"
            class="flex h-[60vh] flex-col items-center justify-center text-center"
          >
            <EmptyState
              :title="selectedGroupId === 'all'
                ? t.overviewPage.emptyAllTitle
                : t.overviewPage.emptyGroupTitle.replace('{group}', selectedGroup?.name || t.devices.groups)"
              :description="selectedGroupId === 'all'
                ? t.overviewPage.emptyAllDesc
                : t.overviewPage.emptyGroupDesc.replace('{group}', selectedGroup?.name || t.devices.groups)"
            >
              <Button v-if="selectedGroupId !== 'all'" variant="outline" size="sm" @click="viewAllDevices">
                {{ t.overviewPage.viewAllDevices }}
              </Button>
            </EmptyState>
          </div>
          <div v-else class="overview-device-grid rounded-lg border border-gray-100 bg-white p-3 shadow-sm">
            <button v-for="device in devices" :key="device.id" type="button"
              class="overview-phone" :style="{ aspectRatio: ratios[device.id] || '540 / 1194' }" :disabled="device.status !== 'online'"
              :aria-label="`远控 ${device.id}`" @click="openDevice(device)">
              <DevicePreview v-if="device.status === 'online'" :device-id="device.id" active :placeholder="t.overviewPage.screenPlaceholder" @dimensions="(w, h) => setRatio(device.id, w, h)" />
              <div v-else class="overview-offline"><X class="h-6 w-6" /><span>{{ device.status === 'pending' ? t.overviewPage.statusPending : t.overviewPage.statusOffline }}</span></div>
              <div class="overview-phone-info"><span class="truncate">{{ device.id }}</span><span :class="cn('h-2 w-2 shrink-0 rounded-full', getStatusDot(device.status))" /></div>
            </button>
          </div>
          <div v-if="loading" class="mt-3 text-xs text-muted-foreground">{{ t.common.loading }}</div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.overview-phone{position:relative;width:100%;aspect-ratio:540/1194;overflow:hidden;border:1px solid #d8e3dc;border-radius:14px;background:#f4f7f5;text-align:left}.overview-phone:focus-visible{outline:2px solid #f97316;outline-offset:2px}.overview-phone :deep([data-preview-state]){background:#f4f7f5}.overview-phone :deep([data-preview-state] > span:last-child){bottom:38px}.overview-offline{display:flex;height:100%;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#7b8d82;font-size:12px}.overview-phone-info{position:absolute;bottom:0;left:0;right:0;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px;color:#20352a;background:rgba(255,255,255,.91);font-size:11px}.overview-device-grid{grid-template-columns:repeat(auto-fill,minmax(160px,200px))}@media(max-width:767px){.overview-device-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
