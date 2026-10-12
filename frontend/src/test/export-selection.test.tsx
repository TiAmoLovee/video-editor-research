import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CandidateResults } from '../components/CandidateResults';
import * as tasks from '../api/tasks';
import type { Candidate, CandidatePage } from '../types/tasks';
import { taskId } from './fixtures';

vi.mock('../components/ExportWorkbench', () => ({
  ExportWorkbench: ({ selected, onMove, onRemove }: { selected: Candidate[]; onMove: (id: string, direction: -1 | 1) => void; onRemove: (id: string) => void }) => <div>
    <span data-testid="draft">{selected.map(item => item.id).join(',')}</span>
    <button onClick={() => selected[1] && onMove(selected[1].id, -1)}>移动第二条</button>
    <button onClick={() => selected[0] && onRemove(selected[0].id)}>移除第一条</button>
  </div>,
}));
function page(offset = 0): CandidatePage {
  return { items: [0, 1].map(index => ({ id: `c${offset + index}`, rank: offset + index + 1, start: 1, end: 21,
    duration_seconds: 20, text: `正文${offset + index}`, score: 50, scorer: 'rule', scoring_status: 'scored', reasons: [], source_sentences: [] })),
  total: 12, offset, limit: 10, has_more: offset === 0, scorer: 'rule', audio_status: 'measured' };
}
describe('candidate selection integration', () => {
  it('retains the chosen order while paging candidates', async () => {
    vi.spyOn(tasks, 'getCandidates').mockImplementation((_id, offset) => Promise.resolve(page(offset)));
    render(<CandidateResults taskId={taskId} download={`/tasks/${taskId}/files/candidates.json`} />);
    fireEvent.click(await screen.findByRole('button', { name: '加入候选 #2' }));
    fireEvent.click(screen.getByRole('button', { name: '加入候选 #1' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页候选' }));
    fireEvent.click(await screen.findByRole('button', { name: '加入候选 #11' }));
    expect(screen.getByTestId('draft')).toHaveTextContent('c1,c0,c10');
    fireEvent.click(screen.getByRole('button', { name: '移动第二条' }));
    expect(screen.getByTestId('draft')).toHaveTextContent('c0,c1,c10');
    fireEvent.click(screen.getByRole('button', { name: '移除第一条' }));
    expect(screen.getByTestId('draft')).toHaveTextContent('c1,c10');
  });
  it('does not carry another task’s draft or pagination into the new task', async () => {
    vi.spyOn(tasks, 'getCandidates').mockResolvedValue(page());
    const view = render(<CandidateResults taskId={taskId} download={`/tasks/${taskId}/files/candidates.json`} />);
    fireEvent.click(await screen.findByRole('button', { name: '加入候选 #1' }));
    const next = '00000000-0000-4000-8000-000000000002';
    view.rerender(<CandidateResults taskId={next} download={`/tasks/${next}/files/candidates.json`} />);
    await screen.findByRole('button', { name: '加入候选 #1' });
    expect(screen.getByTestId('draft')).toBeEmptyDOMElement();
    expect(tasks.getCandidates).toHaveBeenLastCalledWith(next, 0, expect.any(AbortSignal));
  });
});
