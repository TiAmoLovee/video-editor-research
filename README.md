# ClipForge �Ǽ�����

**��������ʦ�������ģ�[�����ѡ����](docs/TOPIC_GENERATION.md)**����ʵ������ǰ�����ڴ������ƶȹȵ�ֶΣ���ѡֻȡ��������Ρ�New1�C4 �Ŀ����ط��� 600 ��������Ϊ 17 ����ѡ��δ���ɵĶ̶μ��״�ʧ����ϱ�����δ���۵��±߽粻���þɱ�ǩ���²�����Ϊ��ȷ���õ�ʵ�飬��δ�滻�������̣��������������ﵽ 85%��

**[���ɺ���˹��޸���](docs/MANUAL_EDIT_EFFORT.md)** ���ڱ��ؽ���ͳһ EDL ����ҳ�༭����Դ֡��������ɾ�����ţ������°汾���������ɻ��ߣ��������վ��仯�͸��Ӱ汾���α仯�����ӱ���ʱ��²��ʱ���� EDL Schema ����ԭ������δ������ռ������ز����ݡ�

**�����ܱ��ؽ�����[���������պ˶�](docs/WEEK5_DELIVERY.md) �� [ͳһ EDL ��ڶ����ν�](docs/WEEK2_TO_WEEK5.md) �� [��ҳ��������ʷ�ָ�](docs/WEB_EXPORT.md)**���Ѵ�ͨ��ȷѡ���ѡ �� ���� EDL �� ��ȷ MP4���ɼ����༭�� MLT�������̶����� 9:16��SRT��ASS ����Ļ��¼����ʵ��ҳ������������ת������ Shotcut ʵ�ʴ������������е�ض���ͨ������ MLT ��Ļ����������ʽ�ֿ����棬ʵ������ȫ���滻�����б༭�����漰ԭ����Ⱦͨ�����ɰ浼��������Ĳ��Լ������顣���� Linux �ĺϳ� EDL ������ HTTP ��Χ��ȡ��ͨ�����û���ȷ������������Ļ������ͬ�����⣬��0.3 ����δ������¼��Test3 �Ĺ̶�����ȡ���ᶪʧ˵���ˣ����鱣���������ߴ��֡��ͨ��������ȡ���ϸ񡣵����� PR #8 �Ѻϲ���2026-10-13 �˶Ա��� 8200 ����ɶ�ȡԭ�����°浼������̨����Щ������鲻���ڵ��������������Ѿ���ꡣ

**�����ܽ׶��Խ�����[��ǰ״̬��������](docs/WEEK4_PLAN.md) �� [��ʷ�˹�����](docs/HUMAN_EVALUATION.md) �� [��ѡ����̨](docs/WEEK4_WORKBENCH.md)**��������ѡ�����Ѻϲ�������֧���������ѡ���Զ���β�Ż����������ʹ�ù������ɸѡ��ѡ����ѡ��ʼ��߽硢���ɿɲ��ź����ص��Ż���Ƶ��New4 ���ز��˹�����Ϊ **17/19��89.47%**���ﵽ�õ����ε� 85% Ŀ�ꣻ[���������������β����](docs/evaluation/new4-20261009/result.json)���������˽��۲�����������Ƶ���ﵽ 85%���������������δ��������͡�

�߽��Ż�Ĭ�Ϲرա�Docker ʹ�� `compose.yaml`��`compose.asr-small.yaml`��`compose.boundary.yaml` �������ã����һ����ѡ������֤�Ĺ�����߲���ȡ `CLIPFORGE_BOUNDARY_CONFIG_FILE` ��˽�µ����� `CLIPFORGE_LLM_API_KEY`������ִ����Ҫ���� `CLIPFORGE_BOUNDARY_REPAIR=1` �� `CLIPFORGE_BOUNDARY_CONFIG`����������Ϊ `config/boundaries/sentence-v1.json`����������ʵ��ʱ����˽�� `.local.json` �н� `initial_used_tokens` ����Ϊ������������Ϊ 91,987�����������»��һ�ݶ�ȡ������˱���������������Ŀ¼�� `boundary-budget.sqlite3`��������Ԥ�������㣬δ֪������ֹͣ�������ã�ʧ�ܲ����ԣ���������ԭ��ѡ���������в��ٵ���ģ�͡�

�Ż���Ƶ��ԭ���֡��˹����շֱ𱣴棬ģ��ѡ�񲻻��Զ���Ϊ�˹�ͨ����ҳ��ġ��߽��Ż��������ɲ鿴�·�Χ��S02 �� S14 �ļ���ʧ�ܼ�¼���ᱻ���ν����д��Դ��Ƶ�������ᷢ�͸�ģ�ͣ����ð�������תд�ͺ�ѡ�߽硣

ClipForge ��ǰ���̣�**��ҳ�ϴ� �� ��̨��һ�� �� ��ͷ��� �� �������� �� �ʼ�תд�����У�� �� ÿ����� 30 ����Ƭ �� ��ѯ�׶ν��� �� Ԥ��������**������������ PySceneDetect ��ͷ���䡢WebRTC VAD �������䣬�ʼ�תд�����ķ־��� analysis.json ���ܣ��Լ� shots.json / vad.json / asr.json ���أ���Ʒ�Բ��ù̶�֡����Ƭ����ͷ����ݲ��ı�ѡ�Ρ�

**�����ܽ�չ��[��ͷ���](docs/SHOT_DETECTION.md) �� [��������](docs/VAD_DETECTION.md) �� [�ʼ�תд���������](docs/ASR_ANALYSIS.md)**��ASR ����ɱ��ؽ��룬�Դ���Ŀ������ Docker ���գ�ʶ���������ʼ�ʱ��������;�ͷ F1 ��δ���ա�

������ʦ�Ĺ��̷���������֧�����ļ�ǰ��Ǩ��Ϊ **React 18 + TypeScript 5 + Vite + Zustand 4 + Ant Design 5**����ˡ����ԺͲ����ļ���Ŀ¼����ͳһ FFmpeg �������á��ڶ���ԭ�����ռ�¼����ʷ֤�ݣ�Ǩ�ƺ����֤��Χ������¼��

**��ʦ�鿴��ڣ�[��������˵��](docs/ENGINEERING_REFACTOR.md)** �� [�ڶ��ܽ���ժҪ](docs/WEEK2_DELIVERY.md) �� [�ܱ�](docs/WEEKLY.md)

## ��Ŀ�ṹ

```text
frontend/                     React ǰ���빹������
  src/components/             �ϴ���������ʷ�����顢��Ƭ���
  src/api/                    HTTP �����ϴ������ص�ַУ��
  src/store/                  Zustand ���񡢷�ҳ��ѡ����������״̬
  src/hooks/                  ��ȡ����������ѯ
  src/types/                  TypeScript �ӿ�����
  src/test/                   ǰ�˽�������
  package.json                ���������ͼ�顢���ԡ���������
  package-lock.json           �ɸ��ְ�װ���������ļ�
backend/
  clipforge/api.py            FastAPI �����ǰ�˹��������ṩ
  clipforge/routes/           HTTP �ϴ�����ѯ������
  clipforge/media/            FFprobe����һ������Ƭ
  clipforge/analysis/         ����У�顢��ͷ��VAD���ʼ� ASR �����ķ־�
  clipforge/services/         ��Ƶ��������
  clipforge/storage/          SQLite ������Ŀ¼
  clipforge/queue/            Celery ���񡢿ͻ��˼�������ʾ
  clipforge/config.py         FFmpeg / FFprobe ���ý���
  tests/                     Python ��Ԫ�뼯��ʵ��
  requirements*.txt           Python ����
deploy/                      API / worker Dockerfile
docs/                        ˵�����ܱ�����ʷ���ռ�¼
schemas/                     ���ݸ�ʽ����
compose.yaml                 ������������
.env.example                 ������������ģ��
```

## Docker �����������������̣�

��װ Git��Docker Desktop������ Linux �������Ȱ� [ASR ˵��](docs/ASR_ANALYSIS.md) ���̶��汾ģ��׼���� models/faster-whisper-base��ģ���� worker ֻ�����ء�Ȼ������Ŀ��Ŀ¼ִ�У�

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

�� [����̨](http://127.0.0.1:8200/)��[�ӿ��ĵ�](http://127.0.0.1:8200/docs)���ڿ�����顣��ַָ��������Լ��ĵ��ԣ�GitHub ��������Щ����

API ������ʹ�� Node.js 24 ���� React ҳ�棬�ٰѾ�̬���︴�Ƶ� Python ����worker �Դ� FFmpeg��ֻ�� Docker ����ʱ���������������ⰲװ Node��Python �� FFmpeg���״ι�����Ҫ���磬������Ҫ���� Docker Desktop ������

**�Ӿɰ�Ǩ��ʱ���ȴ����ڴ�����������ɣ���ͬʱ���¹��� API �� worker**������ Python ģ��·�������仯������ֻ����ҳ�档��������8200 �˿ڡ����� API��Celery ��������SQLite ���ṹ�����ݾ����Ʊ��ֲ��䡣��Ҫɾ�����ݾ�����ʷ���������������ݾ���ȡ������Ǩ�ƺ��ʵ�ʻع��谴����˵��ִ�С�

�Ժ���� `docker compose up -d` �������� `docker compose stop` ֹͣ����������ʱ���� Docker ���к͵��Ի��ѡ�`docker compose down -v` ��ɾ�����ݾ�����������ͨ�رա�

## ���ؿ���

���ؿ���ʹ�� Python 3.11��Node.js 24 �� npm�����ڲֿ��Ŀ¼���� Python ������

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt -r backend/requirements-worker.txt
.\.venv\Scripts\python.exe -m pip install -e backend
```

�༭ǰ��ʱ������������ Docker ������Ϊ API��������һ����ִ�У�

```powershell
cd frontend
npm ci
npm run dev
```

�� `http://127.0.0.1:5173/`��Vite �� `/tasks` ����������� `http://127.0.0.1:8200`���ϴ���������ʹ��ͬһ��ˡ��� API ��ַ��ͬ������ Vite ǰ���� `$env:CLIPFORGE_API_URL`��

���������ԣ��� `frontend` Ŀ¼����

```powershell
npm run typecheck
npm test
npm run build
```

����Ϊ `frontend/dist/`�����ύ Git��Դ�롢package.json �����ļ��ύ Git������ҳ��ʹ�� Vite������ҳ���� FastAPI �ṩ�����������Դ��Ŀ¼ֱ����Ϊ��վ������

������ API ʱ���ڲֿ��Ŀ¼���� `.\.venv\Scripts\python.exe -m uvicorn clipforge.api:app --host 127.0.0.1 --port 8201`�������� Vite �� API Ŀ�ꡣ�÷�ʽ��Ҫ�������ÿ����ӵ� Redis �͹�������Ŀ¼���Ƽ���ѧ�׶�ʹ�� Docker ������̨������������һ������洢��

## ���� FFmpeg ����

Docker �����������ڹ��ߣ����½����� Windows ����ý���������ʹ��ϵͳ PATH �еĿ�ִ���ļ���

```powershell
$env:FFMPEG = (Get-Command ffmpeg -ErrorAction Stop).Source
$env:FFPROBE = (Get-Command ffprobe -ErrorAction Stop).Source
& $env:FFMPEG -version
& $env:FFPROBE -version
```

������߲��� PATH �У��������淽ʽ���뱾��ʵ��·��������ʱ���ö�������ţ���

```powershell
$env:FFMPEG = Read-Host "������ ffmpeg ��ִ���ļ�������·��"
$env:FFPROBE = Read-Host "������ ffprobe ��ִ���ļ�������·��"
& $env:FFMPEG -version
& $env:FFPROBE -version
```

�������ȼ�����������ʽ���� > `FFMPEG` / `FFPROBE` > ���ݾɱ��� `FFMPEG_PATH` / `FFPROBE_PATH` > PATH �е����������ÿ�ε���ʱ��ȡ��`.env.example` ֻ��ģ�壬Python �����Զ���ȡ `.env`��PowerShell �� `$env:` ���������ڵ�ǰ���ڼ����ӽ��̡�

׼���Լ���������Ƶ���ڲֿ��Ŀ¼ִ�У������������·������

```powershell
$video = Read-Host "������Ҫ��������Ƶ����·��"
.\.venv\Scripts\python.exe -m clipforge.media.probe "$video"
.\.venv\Scripts\python.exe -m clipforge.media.normalize "$video" "downloads/normalized.mp4"
.\.venv\Scripts\python.exe -m clipforge.media.split "downloads/normalized.mp4" "downloads/clips"
```

## ��֤�� CI

�ڲֿ��Ŀ¼ִ�У�

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
.\.venv\Scripts\python.exe -m ruff check --no-cache backend
.\.venv\Scripts\python.exe backend/tests/integration_split.py
.\.venv\Scripts\python.exe backend/tests/integration_video_api.py "$video" --timeout 7200
```

���һ����Ҫ�������в������� `$video`���ᴴ��һ����ʵ����`--expected-clips` ���ò���Ԥ�ڣ���������Ƭ��������ȷ��ʱʡ�ԡ��ȴ���ʱ��������ֹ̨ͣ��Ӧ�Ȳ�ѯ�������񣬱����ظ��ϴ���

[GitHub Actions](.github/workflows/ci.yml) �ֱ��� Ubuntu / Windows ��� Python ��ǰ�ˣ�Python ������顢Ruff����Ԫ���ԣ�ǰ�����ļ���װ��TypeScript ��顢�������Ժ� Vite ���������ύ��Զ�̽������ Actions ��¼Ϊ׼��

## ����ɵ���ʷ�����뵱ǰ����

- �ڶ���ԭ������֤��ʵ����Ƶ 2 �Ρ�345.5 ���ز� 12 �Σ���¼�� [ҳ������](docs/samples/web_ui_video_task_verification.json) �� [Test1 ����](docs/samples/Test1_video_task_verification.json)��
- 60 ���� 1080p �͸��ӶȺϳ��ز����� 120 �Σ���ν��빲 108,000 ֡�������¼��ʱԼ 50 �� 18 �룬�� [����Ƶ����](docs/LONG_VIDEO_TEST.md)�����Ǿɰ��ѹ鵵��ý��ʵ�飬��������������ת�����ʵ�����ز����ܡ�
- �ѹ鵵�� [���ݱ�����¼](docs/samples/restart_persistence_verification.json) �� [�쳣����](docs/FAILURE_TESTS.md)����ԭ����ʱ���뷶Χ��
- ��δʵ�ִ����ж��Զ��ָ���ȡ��/���ԡ����û��������Զ���������ֵ�ڴ桢��ȷ����ͬ���������˵�������δ�������׶ΰٷֱȲ�����֡���ȡ�

## ��һ�ܵ��������

��һ����ɻ�������ʵ�顢��ҵ��Ʒ�����¼�������������ͼ�����±���ԭ���н׶�״̬�������ĵ���������Ŀ�꣬����ʾ��ǰ����ȫ��������

| ������ | ��ǰ״̬ |
| --- | --- |
| OpenCut Classic | ����ɱ������С����롢�ָ���ɾ����MP4 ���������ż�ˢ�»ָ����Ѳ���༭�����桢Ԥ������Ƶ�뵼������Ҫģ��ͼ��Դ������ |
| AutoClip | ����ɱ��ز���תд��ģ�ͷ�������Ƭ���ɼ����ţ��Ѳ����������������ͬ������Ȳ�ѯģ��ͼ��ʵ����������·�����쳣��֧��������֤�ݺ˶� |
| Shotcut / MLT | ������ֹ�������MLT ���������´򿪡�MP4 �����벥�ţ����й���������������ʱ���ᡢ���š��˾��ҽӺͺ�̨����ģ��ͼ��Դ������ |
| OpusClip / Descript | �����������Ʒ���������ͼ��OpusClip ����֤ MP4 ���أ�Descript ����������ı�ɾ��������δȷ�� |
| ������˵���� | ���γ� 8 ����������5 ���ǹ������������������оݣ���������˵���ͷֽ׶�ʵ�ַ�Χ |
| ����ͼ��������ʱ��ͼ | ���γ�ͼƬ��˵�����������Ŷ�Ӧ |
| �ֿ��� CI | ������ `.gitignore` �� GitHub Actions �� lint��test ռλ���񣻱��κ˶Ե����гɹ� |

| �ĵ� | ���� |
| --- | --- |
| [���б���](docs/research-report.md) | �ο���Ŀʵ�顢Դ����顢��Ʒ�Ա������� |
| [������˵����](docs/requirements.md) | ���ܡ��ǹ��������������о� |
| [���ͼ˵��](docs/design-diagrams.md) | ������������ʱ������ӳ�� |
| [����ͼ](docs/clipforge-use-case.png) / [ʱ��ͼ](docs/clipforge-main-sequence.png) | ���ͼƬ |
| [Shotcut ��������˵��](docs/samples/README.md) | �ز���Դ��·����������֤��Χ |
| [Week1Work.mlt](docs/samples/Week1Work.mlt) | �ֹ������������� Shotcut �Ͷ�Ӧԭ�ز� |

�ڶ��ܽ�һ��˵����[��һ��](docs/NORMALIZATION.md)��[��Ƭ](docs/SPLITTING.md)��[����](docs/QUEUE.md)��[��Ƶ�ӿ�](docs/VIDEO_TASKS.md)��[��ҳ����](docs/WEB_UI.md)��[�ܱ�](docs/WEEKLY.md)��
