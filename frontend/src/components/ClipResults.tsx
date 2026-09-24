import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, Input } from 'antd';
import { DownloadOutlined, PlayCircleOutlined, SearchOutlined } from '@ant-design/icons';
import { downloadUrl } from '../api/tasks';
import type { Clip, Task, TaskResult } from '../types/tasks';

export function ClipResults({ task, result }: { task: Task; result: TaskResult }) {
  const [query, setQuery] = useState('');
  const [visible, setVisible] = useState(24);
  const [preview, setPreview] = useState<Clip | null>(null);
  const [playError, setPlayError] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const previewPanel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = video.current;
    return () => { if (element) { element.pause(); element.removeAttribute('src'); element.load(); } };
  }, [preview]);
  const matches = useMemo(() => result.clips.map((clip, index) => ({ clip, index })).filter(({ clip, index }) => clip.file.toLowerCase().includes(query.trim().toLowerCase()) || String(index + 1) === query.trim()), [result.clips, query]);
  const seconds = Math.round(result.clips.reduce((sum, clip) => sum + clip.duration_seconds, 0));
  let zip: string;
  try { zip = downloadUrl(result.downloads['result.zip'], task.task_id); }
  catch { return <Alert type="error" message="结果下载地址异常，请刷新任务或检查后台。" />; }
  return <div className="results">
    <div className="result-head"><div><h2>{result.clip_count} 个切片已就绪</h2><p className="hint">总时长 {Math.floor(seconds / 60)} 分 {seconds % 60} 秒 · 下载文件见浏览器下载记录</p></div><Button type="primary" href={zip} download icon={<DownloadOutlined />}>下载全部切片 · ZIP</Button></div>
    {preview && <div className="preview" ref={previewPanel}><div className="head"><strong>{preview.file}</strong><Button size="small" onClick={() => setPreview(null)}>关闭预览</Button></div><video key={preview.file} ref={video} src={downloadUrl(preview.download_url, task.task_id)} controls playsInline preload="none" aria-label="切片预览" onError={() => setPlayError(true)} /><p className="hint">{playError ? '预览暂时不可用，请下载后使用本地播放器观看。' : '点击播放预览，也可以下载后用本地播放器观看。'}</p></div>}
    <div className="clip-toolbar"><Input className="clip-search" prefix={<SearchOutlined />} type="search" allowClear placeholder="搜索切片名称或序号" aria-label="搜索切片名称或序号" value={query} onChange={event => { setQuery(event.target.value); setVisible(24); }} /><span className="clip-count" role="status">显示 {Math.min(visible, matches.length)} / {matches.length} 段</span></div>
    {!matches.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的切片，试试其他名称或序号。" />}
    <div className="clips">{matches.slice(0, visible).map(({ clip, index }) => {
      let url: string;
      try { url = downloadUrl(clip.download_url, task.task_id); }
      catch { return <Alert key={clip.file} type="error" message={`${clip.file} 下载地址异常`} />; }
      return <div className="clip" key={clip.file}><div className="clip-index">{String(index + 1).padStart(2, '0')}</div><div className="clip-info"><div className="clip-name">{clip.file}</div><div className="clip-time">{clip.duration_seconds.toFixed(2)} 秒 · MP4</div></div><div className="clip-actions"><Button type="text" size="small" icon={<PlayCircleOutlined />} aria-label={`预览 ${clip.file}`} onClick={() => { setPreview(clip); setPlayError(false); requestAnimationFrame(() => previewPanel.current?.scrollIntoView({ block: 'nearest' })); }}>预览</Button><Button type="link" size="small" href={url} download aria-label={`下载 ${clip.file}`}>下载 ↓</Button></div></div>;
    })}</div>
    {visible < matches.length && <Button className="load-more" onClick={() => setVisible(value => value + 24)}>显示更多切片</Button>}
    <details className="more"><summary>处理记录与参数文件</summary>{Object.entries({ 'media_meta.json': '原视频参数', 'normalized_media_meta.json': '归一化参数', 'clip_plan.json': '切片清单' }).map(([name, label]) => {
      if (!result.downloads[name]) return null;
      try { return <a key={name} href={downloadUrl(result.downloads[name], task.task_id)} download>{label}</a>; }
      catch { return null; }
    })}</details>
  </div>;
}
