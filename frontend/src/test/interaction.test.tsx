import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { ClipResults } from '../components/ClipResults';
import { TaskDetail } from '../components/TaskDetail';
import { UploadPanel } from '../components/UploadPanel';
import { completedTask, taskId } from './fixtures';
import { useTasks } from '../store/tasks';
import { downloadUrl, fileError } from '../api/tasks';

beforeEach(() => useTasks.setState({ task: null, tasks: [], selectedId: '', detailLoading: false, detailError: '', uploading: false, uploadProgress: 0 }));
describe('file and download boundaries', () => {
  it('rejects empty, unsupported and oversized files', () => {
    expect(fileError(new File([], 'empty.mp4'))).not.toBe('');
    expect(fileError(new File(['data'], 'bad.txt'))).not.toBe('');
    const large = new File(['data'], 'large.mp4');
    Object.defineProperty(large, 'size', { value: 1024 ** 3 + 1 });
    expect(fileError(large)).not.toBe('');
    expect(fileError(new File(['data'], 'ok.MP4'))).toBe('');
  });
  it('rejects external and traversal download URLs', () => {
    for (const url of ['https://example.com/clip.mp4', `/tasks/${taskId}/files/../source.mp4`, `/tasks/${taskId}/files/%2e%2e.mp4`, '/tasks/other/files/clip.mp4']) expect(() => downloadUrl(url, taskId)).toThrow();
    expect(downloadUrl(`/tasks/${taskId}/files/clip_001.mp4`, taskId)).toContain('clip_001.mp4');
  });
});
describe('clip browser', () => {
  it('searches all 120 clips beyond the first batch and handles no matches', () => {
    const task = completedTask(120);
    render(<ClipResults task={task} result={task.result!} />);
    expect(screen.getByText('显示 24 / 120 段')).toBeInTheDocument();
    expect(screen.queryByText('clip_120.mp4')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '显示更多切片' }));
    expect(screen.getByText('显示 48 / 120 段')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '120' } });
    expect(screen.getByText('clip_120.mp4')).toBeInTheDocument();
    expect(screen.getByText('显示 1 / 1 段')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '不存在' } });
    expect(screen.getByText('没有匹配的切片，试试其他名称或序号。')).toBeInTheDocument();
  });
  it('loads preview only on request and releases it on close', () => {
    const task = completedTask();
    render(<ClipResults task={task} result={task.result!} />);
    expect(screen.queryByLabelText('切片预览')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '预览 clip_001.mp4' }));
    const video = screen.getByLabelText('切片预览');
    expect(video).toHaveAttribute('src', `/tasks/${taskId}/files/clip_001.mp4`);
    expect(video).not.toHaveAttribute('autoplay');
    fireEvent.click(screen.getByRole('button', { name: '关闭预览' }));
    expect(video).not.toHaveAttribute('src');
    expect(screen.queryByLabelText('切片预览')).not.toBeInTheDocument();
  });
  it('does not expose stale result links on failed tasks', () => {
    useTasks.setState({ task: { ...completedTask(), status: 'FAILED', stage: 'probing', progress: 10, error: '处理失败' } });
    render(<TaskDetail />);
    expect(screen.queryByRole('link', { name: '下载全部切片 · ZIP' })).not.toBeInTheDocument();
  });
});
describe('upload selection', () => {
  it('requires a valid file, displays its name, and clears selection', async () => {
    const { container } = render(<UploadPanel />);
    expect(screen.getByRole('button', { name: /上传并开始处理/ })).toBeDisabled();
    const input = container.querySelector('input[type=file]')!;
    fireEvent.change(input, { target: { files: [new File(['video'], 'upload.mp4', { type: 'video/mp4' })] } });
    await waitFor(() => expect(screen.getByText('upload.mp4')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /上传并开始处理/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '移除所选视频' }));
    expect(screen.getByRole('button', { name: /上传并开始处理/ })).toBeDisabled();
  });
  it('retains a task identifier without replacing it with navigation anchors', () => {
    vi.spyOn(window.history, 'replaceState');
    useTasks.getState().selectTask(taskId);
    expect(window.location.hash).toBe(`#task=${taskId}`);
    expect(useTasks.getState().selectedId).toBe(taskId);
  });
});
