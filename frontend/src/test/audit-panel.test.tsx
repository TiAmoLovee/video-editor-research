import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AuditPanel } from '../components/AuditPanel';
import * as api from '../api/audit';
import type { AuditPage } from '../types/audit';
import { taskId } from './fixtures';

function page(task = taskId, actor = '小林'): AuditPage {
  return { format_version: 1, task_id: task, unrecorded_actor_edit_count: 0, identity_notice: '姓名自填', events: [
    { kind: 'candidate_review', event_id: '00000000-0000-4000-8000-000000000003', task_id: task, candidate_id: 'c1', decision: 'accepted', actor,
      actor_scope: 'user_supplied_name', created_at: '2026-10-13T00:00:00Z', candidate_snapshot_sha256: 'a'.repeat(64), request_id: '00000000-0000-4000-8000-000000000004',
      before: { decision: 'undecided', actor: null, reason: null, created_at: null, request_id: null },
      after: { decision: 'accepted', actor, reason: '完整', created_at: '2026-10-13T00:00:00Z', request_id: '00000000-0000-4000-8000-000000000004' } },
  ] };
}
describe('human audit', () => {
  it('shows actor, time, before/after and the full task audit download', async () => {
    const request = vi.spyOn(api, 'getAudit').mockResolvedValue(page());
    render(<AuditPanel taskId={taskId} />);
    fireEvent.click(screen.getByText('人工操作记录 · 复核与剪辑修改'));
    expect(await screen.findByText('候选复核 · 小林')).toBeVisible();
    expect(screen.getByText('片段 c1：待复核 → 采纳')).toBeVisible();
    expect(screen.getByText('理由：未填写 → 完整')).toBeVisible();
    expect(document.querySelector('time')).toHaveAttribute('datetime', '2026-10-13T00:00:00Z');
    expect(screen.getByRole('link', { name: '下载完整操作记录' })).toHaveAttribute('href', `/tasks/${taskId}/audit/download`);
    await act(async () => window.dispatchEvent(new CustomEvent('clipforge:audit-changed', { detail: { taskId } })));
    expect(request).toHaveBeenCalledTimes(2);
  });
  it('isolates a late audit response and old task notifications', async () => {
    let resolveOld!: (value: AuditPage) => void;
    const next = '00000000-0000-4000-8000-000000000002';
    const request = vi.spyOn(api, 'getAudit').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; })).mockResolvedValue(page(next, '新任务操作人'));
    const view = render(<AuditPanel taskId={taskId} />);
    view.rerender(<AuditPanel taskId={next} />);
    await screen.findByText('候选复核 · 新任务操作人');
    await act(async () => resolveOld(page(taskId, '旧任务操作人')));
    await act(async () => window.dispatchEvent(new CustomEvent('clipforge:audit-changed', { detail: { taskId } })));
    expect(screen.queryByText(/旧任务操作人/)).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[0][1].aborted).toBe(true);
  });
});
