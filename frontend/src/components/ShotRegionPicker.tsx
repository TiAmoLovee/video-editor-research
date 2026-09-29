import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Alert, Button, Space } from 'antd';
import type { ShotRegion } from '../api/tasks';

export function regionFromPoints(start: [number, number], end: [number, number]): ShotRegion | null {
  const clamp = (value: number) => Math.max(0, Math.min(1, value));
  const [x0, y0, x1, y1] = [...start, ...end].map(clamp);
  const region: ShotRegion = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
  return region[2] - region[0] >= 0.01 && region[3] - region[1] >= 0.01 ? region : null;
}

export function ShotRegionPicker({ file, region, disabled, onChange }: {
  file: File; region: ShotRegion | null; disabled: boolean; onChange: (region: ShotRegion | null) => void;
}) {
  const [url, setUrl] = useState('');
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<ShotRegion | null>(null);
  const [notice, setNotice] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const start = useRef<[number, number] | null>(null);
  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl); setReady(false); setFailed(false); setDrawing(false); setDraft(null); setNotice('');
    start.current = null;
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  useEffect(() => {
    if (disabled) { setDrawing(false); setDraft(null); start.current = null; }
  }, [disabled]);
  function point(event: ReactPointerEvent<HTMLDivElement>): [number, number] {
    const bounds = event.currentTarget.getBoundingClientRect();
    return [(event.clientX - bounds.left) / Math.max(1, bounds.width), (event.clientY - bounds.top) / Math.max(1, bounds.height)];
  }
  const visible = draft || region;
  return <div className="shot-region-picker">
    <p>若素材是浏览器录屏，先拖动进度条找到清晰画面，再框选播放器里的视频。普通视频可使用完整画面。</p>
    <div style={{ position: 'relative', marginBottom: 10 }}>
      {url && <video ref={video} aria-label="本地视频区域预览" src={url} controls muted playsInline preload="metadata"
        style={{ width: '100%', display: 'block', borderRadius: 8 }}
        onLoadedData={() => setReady(true)} onError={() => { setFailed(true); setReady(false); setDrawing(false); onChange(null); }} />}
      {visible && <div aria-label="已选视频区域" style={{ position: 'absolute', pointerEvents: 'none',
        left: `${visible[0]*100}%`, top: `${visible[1]*100}%`, width: `${(visible[2]-visible[0])*100}%`,
        height: `${(visible[3]-visible[1])*100}%`, border: '2px solid #7357da', background: 'rgba(115,87,218,0.08)' }} />}
      {drawing && <div role="img" aria-label="拖动框选视频区域" tabIndex={0}
        style={{ position: 'absolute', inset: 0, cursor: 'crosshair', touchAction: 'none' }}
        onKeyDown={event => { if (event.key === 'Escape') { setDrawing(false); setDraft(null); start.current = null; } }}
        onPointerDown={event => {
          if (disabled || event.button !== 0) return;
          start.current = point(event); setDraft(null);
          event.currentTarget.setPointerCapture?.(event.pointerId);
        }}
        onPointerMove={event => { if (start.current) setDraft(regionFromPoints(start.current, point(event))); }}
        onPointerUp={event => {
          if (!start.current) return;
          const next = regionFromPoints(start.current, point(event)); start.current = null; setDraft(null);
          if (next) { onChange(next); setDrawing(false); setNotice('视频区域已选定，仅用于镜头分析。'); }
          else setNotice('框选区域太小，请重新拖动选择。');
        }}
        onPointerCancel={() => { start.current = null; setDraft(null); }} />}
    </div>
    {failed && <Alert type="info" message="浏览器无法预览此文件。可以使用完整画面检测，或转换为 MP4 后再框选。" />}
    <Space wrap>
      <Button disabled={disabled || !ready || failed} onClick={() => {
        video.current?.pause(); setDrawing(!drawing); setDraft(null); start.current = null;
      }}>{drawing ? '取消框选' : '框选视频区域'}</Button>
      <Button disabled={disabled} onClick={() => {
        onChange(null); setDrawing(false); setDraft(null); start.current = null; setNotice('将检测完整画面。');
      }}>使用完整画面</Button>
    </Space>
    <p role="status">{drawing ? '按住鼠标拖出矩形，松开完成；按 Esc 取消。' : notice || (region ? '已选择视频区域。' : '当前检测完整画面。')}</p>
  </div>;
}
