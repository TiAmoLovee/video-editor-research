import { create } from 'zustand';
import { validId } from '../api/tasks';
import type { Task, TaskSummary } from '../types/tasks';

export function taskFromHash(): string {
  const value = new URLSearchParams(window.location.hash.slice(1)).get('task') || '';
  return validId(value) ? value : '';
}
interface TaskStore {
  selectedId: string;
  tasks: TaskSummary[];
  task: Task | null;
  offset: number;
  hasMore: boolean;
  loading: boolean;
  detailLoading: boolean;
  detailError: string;
  connectionError: string;
  connected: boolean;
  refreshKey: number;
  uploading: boolean;
  uploadProgress: number;
  selectTask: (id: string, scroll?: boolean) => void;
  setOffset: (offset: number) => void;
  refresh: () => void;
}
export const useTasks = create<TaskStore>()((set, get) => ({
  selectedId: taskFromHash(), tasks: [], task: null, offset: 0,
  hasMore: false, loading: true, detailLoading: false, detailError: '',
  connectionError: '', connected: false, refreshKey: 0, uploading: false, uploadProgress: 0,
  selectTask: (id, scroll = false) => {
    if (!validId(id)) return;
    const changed = get().selectedId !== id;
    set({ selectedId: id, ...(changed ? { task: null, detailLoading: true, detailError: '' } : {}), refreshKey: get().refreshKey + 1 });
    window.history.replaceState(null, '', `#task=${id}`);
    if (scroll && window.matchMedia('(max-width: 660px)').matches) document.getElementById('task-detail')?.scrollIntoView({ block: 'start' });
  },
  setOffset: offset => set({ offset: Math.max(0, offset), loading: true }),
  refresh: () => set({ refreshKey: get().refreshKey + 1 }),
}));
