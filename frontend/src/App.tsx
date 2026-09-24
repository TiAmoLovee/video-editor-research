import { Alert } from 'antd';
import { AppstoreOutlined, ClockCircleOutlined, QuestionCircleOutlined, ScissorOutlined, VideoCameraOutlined, DownloadOutlined, SafetyOutlined } from '@ant-design/icons';
import { UploadPanel } from './components/UploadPanel';
import { TaskHistory } from './components/TaskHistory';
import { TaskDetail } from './components/TaskDetail';
import { useTaskPolling } from './hooks/useTaskPolling';
import { useTasks } from './store/tasks';

export default function App() {
  useTaskPolling();
  const { connected, connectionError } = useTasks();
  function navigate(id: string) {
    const target = document.getElementById(id);
    if (target instanceof HTMLDetailsElement) target.open = true;
    target?.scrollIntoView({ block: 'start' });
  }
  return <>
    <a className="skip-link" href="#workspace" onClick={event => { event.preventDefault(); navigate('workspace'); }}>跳到视频工作台</a>
    <aside className="sidebar" aria-label="主导航"><div><a className="brand" href="/"><span className="brand-mark"><ScissorOutlined /></span>ClipForge</a><div className="brand-sub">视频 · 智剪工坊</div></div><nav aria-label="工作台导航"><div className="nav-caption">WORKSPACE</div>{[{ id: 'workspace', label: '视频工作台', icon: <AppstoreOutlined /> }, { id: 'history-panel', label: '历史任务', icon: <ClockCircleOutlined /> }, { id: 'help', label: '使用指南', icon: <QuestionCircleOutlined /> }].map(item => <a key={item.id} className={`nav-link${item.id === 'workspace' ? ' active' : ''}`} href={`#${item.id}`} onClick={event => { event.preventDefault(); navigate(item.id); }}>{item.icon}{item.label}</a>)}</nav><div className="sidebar-bottom"><div className="local-card"><div className="local-title"><span className="dot" />在你的电脑上处理</div>素材与成品存储在本机，<br />随时回来，继续下载。</div><div className="version">CLIPFORGE / 本地工作台</div></div></aside>
    <div className="app-shell"><header className="topbar"><div className="breadcrumb">工作空间 <span>/</span><strong>视频工作台</strong></div><div className="top-actions"><span className={`service-status${connectionError ? ' offline' : ''}`} role="status"><span className="dot" />{connectionError ? '连接暂时中断' : connected ? '服务已连接' : '正在连接服务'}</span><span className="avatar" aria-hidden>CF</span></div></header><main id="workspace">
      <div className="intro"><div><div className="eyebrow">YOUR VIDEO, IN PIECES.</div><h1>让每一段，都刚刚好。</h1><p>放入一个视频，剩下的交给 ClipForge。处理完成，即可预览与下载。</p></div><div className="intro-tag">专注创作，简化剪辑</div></div>
      <div className="recipe" aria-label="当前处理规则">{[{ icon: <VideoCameraOutlined />, title: '通用 MP4 格式', text: 'H.264 视频 · 有音轨时保留音频' }, { icon: <ScissorOutlined />, title: '每段最多 30 秒', text: '自动分段 · 不丢最后的尾片' }, { icon: <DownloadOutlined />, title: '自由选择下载', text: '单段视频 · 全部打包 ZIP' }].map(item => <div className="recipe-item" key={item.title}><span className="recipe-icon">{item.icon}</span><div><strong>{item.title}</strong><small>{item.text}</small></div></div>)}</div>
      {connectionError && <Alert className="connection-notice" type="warning" showIcon message={connectionError} />}
      <div className="workspace"><aside><UploadPanel /><TaskHistory /><div className="aside-note"><SafetyOutlined /><div><strong>任务会保留在这台电脑上。</strong><br />上传结束后可以关闭页面。处理期间请保持后台服务运行。</div></div></aside><TaskDetail /></div>
      <details className="panel help" id="help"><summary>使用小贴士 · 关于处理、预览与下载</summary><p>选择或拖入一个视频，再点击“上传并开始处理”。任务在后台依次转码、切片和打包，页面每 3 秒更新一次阶段状态。长视频可能需要较长时间，上传完成后可以从历史任务继续查看。</p><p>切片支持在线播放预览。如果浏览器不支持播放，请下载后用本地播放器打开。下载文件可以在浏览器的“下载记录”中找到。任务和文件保存在当前电脑上，刷新网页不会删除它们。</p></details>
      <footer><span>ClipForge · 留住每一段精彩</span><a href="/docs" target="_blank" rel="noopener">开发用接口文档 ↗</a></footer>
    </main></div>
  </>;
}
