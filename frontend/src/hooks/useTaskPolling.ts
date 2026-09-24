import { useEffect } from 'react';
import { ApiError, getTask, getTasks } from '../api/tasks';
import { taskFromHash, useTasks } from '../store/tasks';

export function useTaskPolling(): void {
  const selectedId = useTasks(state => state.selectedId);
  const offset = useTasks(state => state.offset);
  const refreshKey = useTasks(state => state.refreshKey);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const page = await getTasks(offset, controller.signal);
        if (controller.signal.aborted) return;
        useTasks.setState({ tasks: page.items, hasMore: page.has_more, loading: false, connected: true, connectionError: '' });
        if (!selectedId && page.items.length) {
          useTasks.getState().selectTask(page.items[0].task_id);
          return;
        }
        if (selectedId) {
          try {
            const task = await getTask(selectedId, controller.signal);
            if (!controller.signal.aborted && useTasks.getState().selectedId === selectedId) {
              useTasks.setState(previous => ({ task: JSON.stringify(previous.task) === JSON.stringify(task) ? previous.task : task, detailLoading: false, detailError: '' }));
            }
          } catch (error) {
            if (error instanceof ApiError && error.status === 404 && !controller.signal.aborted) {
              useTasks.setState({ task: null, detailLoading: false, detailError: '找不到这个任务，请选择其他历史任务。' });
            } else throw error;
          }
        }
      } catch {
        if (!controller.signal.aborted) useTasks.setState({ connected: false, loading: false, connectionError: '暂时无法更新任务，请确认后台服务运行。页面会自动重试。' });
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 3000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selectedId, offset, refreshKey]);
  useEffect(() => {
    const onHash = () => {
      const id = taskFromHash();
      if (id) useTasks.getState().selectTask(id);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
}
