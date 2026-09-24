import type { Task, TaskPage } from '../types/tasks';

export const validId = (value: string): boolean => /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function request<T>(path: string, signal: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timeout = window.setTimeout(abort, 10000);
  try {
    const response = await fetch(path, { signal: controller.signal, cache: 'no-store' });
    const body = await response.json();
    if (!response.ok) throw new ApiError(typeof body.detail === 'string' ? body.detail : '服务暂时无法响应', response.status);
    return body as T;
  } finally {
    window.clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
  }
}
export const getTasks = (offset: number, signal: AbortSignal) => request<TaskPage>(`/tasks?limit=10&offset=${offset}`, signal);
export const getTask = (id: string, signal: AbortSignal) => request<Task>(`/tasks/${id}`, signal);

export function downloadUrl(url: string, taskId: string): string {
  // Only the same task's single filename is accepted, including for media previews.
  const prefix = `/tasks/${taskId}/files/`;
  const name = typeof url === 'string' && url.startsWith(prefix) ? url.slice(prefix.length) : '';
  if (!validId(taskId) || !/^[a-zA-Z0-9_-]+\.(mp4|zip|json)$/.test(name)) throw new Error('下载地址格式异常');
  return url;
}

export function fileError(file: File | null): string {
  if (!file) return '请选择一个视频。';
  if (!file.size || file.size > 1024 ** 3) return '请选择非空且不超过 1 GiB 的视频。';
  if (!/\.(mp4|mov|mkv|webm|m4v|avi)$/i.test(file.name)) return '请选择支持的视频格式。';
  return '';
}

export interface UploadReceipt { taskId?: string; warning?: string }
export function uploadVideo(file: File, onProgress: (value: number) => void): Promise<UploadReceipt> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/tasks');
    // A large local upload has no arbitrary short timeout; network errors remain explicit.
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    };
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        const id = typeof data.task_id === 'string' && validId(data.task_id) ? data.task_id : undefined;
        if (xhr.status === 202 && id) resolve({ taskId: id });
        else if (id) resolve({ taskId: id, warning: typeof data.detail === 'string' ? data.detail : '提交结果待确认，请先查询此任务，避免重复上传。' });
        else reject(new Error(typeof data.detail === 'string' ? data.detail : '未能确认提交结果，请先刷新任务列表，避免重复上传。'));
      } catch { reject(new Error('未能确认提交结果，请先刷新任务列表，避免重复上传。')); }
    };
    xhr.onerror = () => reject(new Error('上传连接中断，请先刷新任务列表确认是否已创建任务，避免重复上传。'));
    xhr.onabort = () => reject(new Error('上传已中断，请先查看任务列表确认结果。'));
    const body = new FormData();
    body.append('file', file);
    xhr.send(body);
  });
}
