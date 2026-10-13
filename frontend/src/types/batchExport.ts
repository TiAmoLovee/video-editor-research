import type { Profile } from './editing';

export interface BatchExportRecord {
  format_version: 1;
  artifact_kind: 'edl_batch_clip_export';
  task_id: string;
  revision_id: string;
  export_id: string;
  request_id: string;
  created_at: string;
  edl_file_sha256: string;
  edl_content_sha256: string;
  source_sha256: string;
  profile: Profile;
  clip_count: number;
  total_frames: number;
  output_bytes: number;
  files: Record<'clips.zip' | 'source.edl.json' | 'manifest.json', string>;
}
export interface BatchExportHistory { items: BatchExportRecord[]; unavailable_count: number }
