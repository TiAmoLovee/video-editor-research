export interface Profile { width: number; height: number; fps: { num: number; den: number } }
export interface FramingSource { source_id: string; x: number; y: number; width: number; height: number }
export type EdlFraming = { mode: 'center_crop'; sources: FramingSource[] }
  | { mode: 'fit_pad'; background: 'black'; sources: FramingSource[] };
export interface EdlRevision {
  task_id: string;
  revision_id: string;
  created_at: string;
  clip_count: number;
  duration_frames: number;
  files?: Record<string, string>;
  derivation?: { kind: string; parent_revision_id: string };
}
export interface EdlSummary extends EdlRevision { profile: Profile; selected_ids: string[] }
export interface EdlPage {
  items: EdlSummary[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  unavailable_count: number;
}
export interface EditedClip {
  candidate_id: string;
  source_in_frame: number;
  source_out_frame: number;
}
export interface EditEffort {
  net_boundary_adjustment_seconds: number;
  added_clip_boundary_adjustment_seconds?: number;
  adjusted_clip_count: number;
  added_clip_count: number;
  removed_clip_count: number;
  minimum_moved_clip_count: number;
}
export interface EditRecord {
  actor?: string | null;
  created_at?: string;
  baseline_revision_id: string;
  parent_revision_id: string;
  editing_elapsed_ms: number | null;
  effort: EditEffort;
  step_effort: EditEffort;
}
export interface SavedEdl {
  revision: EdlRevision;
  edl: {
    schema_version: string;
    profile: Profile;
    framing?: EdlFraming;
    sources?: { id: string; width: number; height: number }[];
    duration_frames: number;
    clips: {
      id: string;
      source_id: string;
      source_in_frame: number;
      source_out_frame: number;
      timeline_in_frame: number;
      timeline_out_frame: number;
      provenance?: { candidate_id: string };
    }[];
  };
  manifest: unknown;
  edit_record?: EditRecord;
  available_clips?: EditedClip[];
}
interface VersionRecord {
  task_id: string;
  revision_id: string;
  created_at: string;
  files?: Record<string, string>;
}
export interface SubtitleRecord extends VersionRecord { subtitle_id: string; cue_count: number; font_name: string }
export interface RenderRecord extends VersionRecord {
  render_id: string;
  profile: Profile;
  total_frames: number;
  output_bytes: number;
  subtitle_id?: string;
}
export interface MltRecord extends VersionRecord {
  export_id: string;
  profile: Profile;
  total_frames: number;
  clip_count: number;
  subtitle_id?: string;
  cue_count?: number;
  subtitle_font_name?: string;
}
export interface ArtifactHistory {
  subtitles: SubtitleRecord[];
  renders: RenderRecord[];
  mlt_exports: MltRecord[];
  unavailable_count: number;
}
