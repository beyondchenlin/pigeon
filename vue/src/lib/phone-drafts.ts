import { reactive } from 'vue';
// In-memory only: no private message text in storage, URLs, telemetry or logs.
export const phoneDrafts = reactive(new Map<string, string>());
