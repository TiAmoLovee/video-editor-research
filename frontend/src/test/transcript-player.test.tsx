import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TranscriptPlayer } from '../components/TranscriptPlayer';
import * as api from '../api/transcript';
import type { Transcript } from '../api/transcript';
import { taskId } from './fixtures';

const download = `/tasks/${taskId}/files/analysis.json`;
function transcript(text = '第一句话'): Transcript {
  return { media: { duration_seconds: 40, time_reference: 'normalized_video', normalized_sha256: 'a'.repeat(64) },
    sentences: [{ id: 's1', start: 12.5, end: 18, text }, { id: 's2', start: 21, end: 29, text: '第二句话' }] };
}
describe('transcript and source video', () => {
  it('seeks the source to the clicked sentence and follows playback with accessible state', async () => {
    vi.spyOn(api, 'getTranscript').mockResolvedValue(transcript());
    render(<TranscriptPlayer taskId={taskId} download={download} />);
    const first = await screen.findByRole('button', { name: '跳转第 1 句，12.50 秒：第一句话' });
    const video = screen.getByLabelText('原视频播放器') as HTMLVideoElement;
    expect(video).toHaveAttribute('src', `/tasks/${taskId}/source`);
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 });
    fireEvent.click(first);
    expect(video.currentTime).toBe(12.5);
    expect(first).toHaveAttribute('aria-current', 'true');
    video.currentTime = 22; fireEvent.timeUpdate(video);
    expect(first).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('button', { name: '跳转第 2 句，21.00 秒：第二句话' })).toHaveAttribute('aria-current', 'true');
  });
  it('keeps the clicked seek until source metadata is ready', async () => {
    vi.spyOn(api, 'getTranscript').mockResolvedValue(transcript());
    render(<TranscriptPlayer taskId={taskId} download={download} />);
    const video = screen.getByLabelText('原视频播放器') as HTMLVideoElement;
    fireEvent.click(await screen.findByRole('button', { name: /跳转第 2 句/ }));
    expect(video.currentTime).toBe(0);
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 }); fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(21);
    expect(screen.getByText('已跳到 21.00 秒，点击视频播放即可试听。')).toBeInTheDocument();
  });
  it('isolates delayed transcript results and media events after selecting another task', async () => {
    let resolveOld!: (value: Transcript) => void;
    const request = vi.spyOn(api, 'getTranscript').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; })).mockResolvedValue(transcript('新任务文字'));
    const view = render(<TranscriptPlayer taskId={taskId} download={download} />);
    const oldVideo = screen.getByLabelText('原视频播放器') as HTMLVideoElement;
    const nextId = '00000000-0000-4000-8000-000000000002';
    view.rerender(<TranscriptPlayer taskId={nextId} download={`/tasks/${nextId}/files/analysis.json`} />);
    const currentVideo = screen.getByLabelText('原视频播放器') as HTMLVideoElement;
    await screen.findByRole('button', { name: /新任务文字/ });
    Object.defineProperty(oldVideo, 'readyState', { configurable: true, value: 1 });
    oldVideo.currentTime = 24; fireEvent.loadedMetadata(oldVideo); fireEvent.timeUpdate(oldVideo); fireEvent.error(oldVideo);
    await act(async () => resolveOld(transcript('旧任务迟到文字')));
    expect(screen.queryByText(/旧任务迟到文字/)).not.toBeInTheDocument();
    expect(currentVideo.currentTime).toBe(0);
    expect(currentVideo).toHaveAttribute('src', `/tasks/${nextId}/source`);
    expect(screen.queryByText(/原视频暂时无法播放/)).not.toBeInTheDocument();
    expect(request.mock.calls[0][2].aborted).toBe(true);
    expect(oldVideo).not.toHaveAttribute('src');
  });
  it('does not carry a queued seek into another source', async () => {
    vi.spyOn(api, 'getTranscript').mockResolvedValue(transcript());
    const view = render(<TranscriptPlayer taskId={taskId} download={download} />);
    fireEvent.click(await screen.findByRole('button', { name: /跳转第 2 句/ }));
    const nextId = '00000000-0000-4000-8000-000000000002';
    view.rerender(<TranscriptPlayer taskId={nextId} download={`/tasks/${nextId}/files/analysis.json`} />);
    await screen.findByRole('button', { name: /跳转第 2 句/ });
    const video = screen.getByLabelText('原视频播放器') as HTMLVideoElement;
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 }); fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(0);
  });
});
