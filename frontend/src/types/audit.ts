import type { ReviewDecision } from './reviews';

interface AuditBase { event_id: string; task_id: string; actor: string | null; actor_scope: 'user_supplied_name' | 'unrecorded'; created_at: string; candidate_snapshot_sha256: string }
interface ReviewState { decision: ReviewDecision; actor: string | null; reason: string | null; created_at: string | null; request_id: string | null }
export interface ReviewAuditEvent extends AuditBase {
  kind: 'candidate_review'; candidate_id: string; decision: ReviewDecision; request_id: string;
  before: ReviewState; after: ReviewState;
}
export interface AuditClip { candidate_id: string; position: number; source_in_frame: number; source_out_frame: number }
export type EditOperation = { kind: 'boundary_changed' | 'added' | 'removed'; candidate_id: string; before: AuditClip | null; after: AuditClip | null }
  | { kind: 'reordered'; before: string[]; after: string[] };
export interface EditAuditEvent extends AuditBase {
  kind: 'manual_edit' | 'edl_adoption'; revision_id: string; parent_revision_id?: string;
  before: { duration_frames: number; clips: AuditClip[] }; after: { duration_frames: number; clips: AuditClip[] };
  operations: EditOperation[];
}
export type AuditEvent = ReviewAuditEvent | EditAuditEvent;
export interface AuditPage { format_version: 1; task_id: string; events: AuditEvent[]; unrecorded_actor_edit_count: number; unrecorded_actor_event_count?: number; identity_notice: string }
