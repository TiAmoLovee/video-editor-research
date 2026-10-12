export interface Profile { width: number; height: number; fps: { num: number; den: number } }
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
export interface SavedEdl {
  revision: EdlRevision;
  edl: {
    schema_version: string;
    profile: Profile;
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
