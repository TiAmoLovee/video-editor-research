import type { Task } from '../types/tasks';
export const taskId = '00000000-0000-4000-8000-000000000001';
export function completedTask(count = 2): Task {
  return { task_id: taskId, source_name: 'sample.mp4', status: 'SUCCEEDED', stage: 'done', progress: 100, created_at: '2026-09-24T00:00:00Z', updated_at: '2026-09-24T00:00:01Z', error: null, progress_kind: 'stage_estimate', result: {
    clip_count: count, total_frames: count * 900,
    downloads: { 'result.zip': `/tasks/${taskId}/files/result.zip` },
    clips: Array.from({ length: count }, (_, index) => ({ file: `clip_${String(index + 1).padStart(3, '0')}.mp4`, start_frame: index * 900, end_frame: (index + 1) * 900, frame_count: 900, duration_seconds: 30, download_url: `/tasks/${taskId}/files/clip_${String(index + 1).padStart(3, '0')}.mp4` })),
  } };
}
