import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ShotRegionPicker, regionFromPoints } from '../components/ShotRegionPicker';
import { uploadVideo } from '../api/tasks';
import { taskId } from './fixtures';

beforeEach(() => {
  vi.stubGlobal('PointerEvent', MouseEvent);
  URL.createObjectURL = vi.fn(() => 'blob:local-preview');
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('per-video shot region', () => {
  it('normalizes reverse drag and clamps to the visible frame', () => {
    expect(regionFromPoints([0.8, 0.9], [-0.1, 0.2])).toEqual([0, 0.2, 0.8, 0.9]);
    expect(regionFromPoints([0.2, 0.2], [0.201, 0.9])).toBeNull();
  });
  it('captures a drag, resets to full frame, and releases the local preview', () => {
    const change = vi.fn();
    const view = render(<ShotRegionPicker file={new File(['video'], 'test.mp4')} region={null} disabled={false} onChange={change} />);
    fireEvent.loadedData(screen.getByLabelText('本地视频区域预览'));
    fireEvent.click(screen.getByRole('button', { name: '框选视频区域' }));
    const overlay = screen.getByRole('img', { name: '拖动框选视频区域' });
    vi.spyOn(overlay, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 200, height: 100 } as DOMRect);
    fireEvent.pointerDown(overlay, { button: 0, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(overlay, { clientX: 180, clientY: 80 });
    fireEvent.pointerUp(overlay, { clientX: 180, clientY: 80 });
    expect(change).toHaveBeenLastCalledWith([0.1, 0.2, 0.9, 0.8]);
    fireEvent.click(screen.getByRole('button', { name: '使用完整画面' }));
    expect(change).toHaveBeenLastCalledWith(null);
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-preview');
  });
  it('sends settings with only the upload that requested them', async () => {
    const bodies: FormData[] = [];
    class FakeRequest {
      status = 202; responseText = JSON.stringify({ task_id: taskId }); upload = {}; onload?: () => void;
      open() {}
      send(body: FormData) { bodies.push(body); this.onload?.(); }
    }
    vi.stubGlobal('XMLHttpRequest', FakeRequest);
    const file = new File(['video'], 'test.mp4');
    await uploadVideo(file, () => {}, { method: 'robust', region: [0.1, 0.2, 0.9, 0.8] });
    await uploadVideo(file, () => {});
    expect(JSON.parse(bodies[0].get('shot_options') as string)).toEqual({ method: 'robust', region: [0.1, 0.2, 0.9, 0.8] });
    expect(bodies[1].has('shot_options')).toBe(false);
  });
});
