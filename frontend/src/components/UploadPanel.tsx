import { useState } from 'react';
import { Alert, Button, Progress, Upload } from 'antd';
import { InboxOutlined, UploadOutlined } from '@ant-design/icons';
import { fileError, uploadVideo } from '../api/tasks';
import { useTasks } from '../store/tasks';

export function UploadPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState('');
  const [failed, setFailed] = useState(false);
  const uploading = useTasks(state => state.uploading);
  const progress = useTasks(state => state.uploadProgress);
  async function submit() {
    const error = fileError(file);
    if (error || !file || uploading) { setNotice(error); setFailed(true); return; }
    useTasks.setState({ uploading: true, uploadProgress: 0 });
    setNotice('正在上传，请保持当前页面打开。'); setFailed(false);
    try {
      const receipt = await uploadVideo(file, value => useTasks.setState({ uploadProgress: value }));
      if (receipt.taskId) {
        useTasks.setState({ offset: 0 });
        useTasks.getState().selectTask(receipt.taskId, true);
      }
      setNotice(receipt.warning || '任务已提交，可在“处理与下载”查看进度。');
      setFailed(!!receipt.warning);
      setFile(null);
    } catch (error) { setNotice(error instanceof Error ? error.message : '上传失败，请检查任务列表后重试。'); setFailed(true); }
    finally { useTasks.setState({ uploading: false }); }
  }
  return <section className="panel pad upload-panel" aria-labelledby="upload-heading">
    <h2 id="upload-heading"><span className="step">01</span>添加视频</h2>
    <p className="section-sub">从一份素材，开始你的下一次创作。</p>
    <div className="react-upload" aria-busy={uploading}>
      <Upload.Dragger accept=".mp4,.mov,.mkv,.webm,.m4v,.avi" multiple={false} maxCount={1} disabled={uploading} showUploadList={false} fileList={[]}
        beforeUpload={candidate => {
          const error = fileError(candidate); setNotice(error); setFailed(!!error);
          setFile(error ? null : candidate);
          return Upload.LIST_IGNORE;
        }}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">拖拽视频到这里，或点击选择</p>
        <p className="ant-upload-hint">MP4 / MOV / MKV / WebM / M4V / AVI<br />每次一个文件，最大 1 GiB</p>
      </Upload.Dragger>
      {file && <div className="selection"><div><div className="selected-file">{file.name}</div><small>{(file.size / 1024 ** 2).toFixed(1)} MB · 已准备好上传</small></div><Button type="text" aria-label="移除所选视频" disabled={uploading} onClick={() => { setFile(null); setNotice(''); }}>×</Button></div>}
      <Button type="primary" block size="large" icon={<UploadOutlined />} loading={uploading} disabled={!file || uploading} onClick={() => void submit()}>上传并开始处理</Button>
      {uploading && <Progress percent={progress} size="small" aria-label="上传进度" />}
      <div role="status" aria-live="polite">{notice && <Alert className="upload-notice" type={failed ? 'warning' : 'info'} message={uploading && progress === 100 ? '上传完毕，正在确认任务…' : notice} showIcon />}</div>
    </div>
  </section>;
}
