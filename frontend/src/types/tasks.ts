export type TaskState = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'SUBMISSION_UNKNOWN';
export type Stage = 'queued' | 'probing' | 'normalizing' | 'analyzing_shots' | 'analyzing_speech' | 'transcribing' | 'combining_analysis' | 'scoring_candidates' | 'splitting' | 'packaging' | 'done';
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
export interface Candidate {
  id: string;
  rank: number;
  start: number;
  end: number;
  duration_seconds: number;
  text: string;
  score: number;
  scorer: 'rule' | 'llm';
  rule_score?: number | null;
  scoring_status: 'scored';
  reasons: string[];
  source_sentences: string[];
}
export interface CandidatePage {
  items: Candidate[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  scorer: 'rule' | 'llm';
  requested_scorer?: 'rule' | 'llm';
  fallback_reason?: string | null;
  audio_status: 'measured' | 'unavailable' | 'no_audio';
}
export const statusLabels: Record<TaskState, string> = {
  QUEUED: '等待处理', RUNNING: '处理中', SUCCEEDED: '已完成',
  FAILED: '处理失败', SUBMISSION_UNKNOWN: '提交待确认',
};
export const stageLabels: Record<Stage, string> = {
  queued: '等待后台领取', probing: '读取视频参数', normalizing: '归一化视频',
  analyzing_shots: '检测镜头边界', analyzing_speech: '检测语音活动', transcribing: '语音转文字', combining_analysis: '校验分析结果',
  scoring_candidates: '生成候选并评分',
  splitting: '生成切片', packaging: '整理下载文件', done: '处理完成',
};
