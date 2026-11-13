export type BrowseExecution = {
  version: 1; kind: 'browse'; duration_s: number; clock: 'elapsed';
  finish_timeout_s: number; stop_timeout_s: number; progress_interval_s: number; event_latency_s: number;
  budget: { max_tokens: number };
  progress_requirements: { min_observations: number; min_active_s: number; max_stall_s: number };
};
export type TaskIdentity = { taskId: string; requestId: string };
export type TaskRequest = TaskIdentity & { body: { goal: string; request_id: string; timeout_s: number; execution?: BrowseExecution } };
export type MergeableReceipt = { task_id: string; status: string; updated_at?: number; cancel_requested?: boolean };
export function validateExecution(value: unknown): BrowseExecution;
export function supervisionSeconds(execution: BrowseExecution): number;
export function prepareTask(domain: string, goal: string, requestId?: string, options?: { timeout_s?: number; execution?: BrowseExecution }): Promise<TaskRequest>;
export function dispatchTask<T>(request: TaskRequest, persist: (identity: TaskIdentity) => unknown, post: (body: TaskRequest['body']) => Promise<T>): Promise<T>;
export function terminalTask(status: string): boolean;
export function stoppingTask(task: MergeableReceipt): boolean;
export function mergeTaskReceipt<T extends MergeableReceipt>(previous: T | undefined, incoming: T): T;
export function mergeTaskReceipts<T extends MergeableReceipt>(previous: T[], incoming: T[]): T[];
export function taskProgress(task: { status: string; stale?: boolean; result?: { progress?: unknown; step?: unknown } }, connectionUncertain?: boolean): { progress: string; step: string; stale: boolean };
