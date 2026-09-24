export type TaskState = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'SUBMISSION_UNKNOWN';
export type Stage = 'queued' | 'probing' | 'normalizing' | 'splitting' | 'packaging' | 'done';
export interface TaskSummary {
  task_id: string;
  source_name: string;
  status: TaskState;
  stage: Stage;
  progress: number;
  created_at: string;
  updated_at: string;
}
export interface Clip {
  file: string;
  start_frame: number;
  end_frame: number;
  frame_count: number;
  duration_seconds: number;
  download_url: string;
}
export interface TaskResult {
  clip_count: number;
  total_frames: number;
  clips: Clip[];
  downloads: Record<string, string>;
}
export interface Task extends TaskSummary {
  error: string | null;
  progress_kind: 'stage_estimate';
  result: TaskResult | null;
}
export interface TaskPage {
  items: TaskSummary[];
  has_more: boolean;
  limit: number;
  offset: number;
}
export const statusLabels: Record<TaskState, string> = {
  QUEUED: '等待处理', RUNNING: '处理中', SUCCEEDED: '已完成',
  FAILED: '处理失败', SUBMISSION_UNKNOWN: '提交待确认',
};
export const stageLabels: Record<Stage, string> = {
  queued: '等待后台领取', probing: '读取视频参数', normalizing: '归一化视频',
  splitting: '生成切片', packaging: '整理下载文件', done: '处理完成',
};
