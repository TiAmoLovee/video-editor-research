export type ReviewDecision = 'accepted' | 'rejected' | 'undecided';

export interface CandidateReview {
  candidate_id: string;
  decision: ReviewDecision;
  actor: string | null;
  reason: string | null;
  created_at: string | null;
  request_id: string | null;
}

export interface ReviewPage {
  format_version: 1;
  task_id: string;
  candidate_snapshot_sha256: string;
  items: CandidateReview[];
}

export interface ReviewEvent {
  event_id: string;
  kind: 'candidate_review';
  task_id: string;
  candidate_id: string;
  decision: ReviewDecision;
  actor: string;
  actor_scope: 'user_supplied_name';
  reason: string | null;
  created_at: string;
  candidate_snapshot_sha256: string;
  request_id: string;
  before: { decision: ReviewDecision };
  after: { decision: ReviewDecision };
}

export interface ReviewWrite {
  candidate_id: string;
  decision: ReviewDecision;
  actor: string;
  reason: string | null;
  request_id: string;
  expected_snapshot_sha256: string;
}
