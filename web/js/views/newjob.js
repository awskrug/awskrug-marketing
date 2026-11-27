import { h } from '../dom.js';
import { createJob, startJob } from '../api.js';
import { uploadFile } from '../upload.js';
import { checkFileSize, formatProgress, formatMB } from '../status.js';

export function newJobView(root) {
  const fileInput = h('input', { id: 'file', type: 'file', accept: 'video/*,.mp4,.mov,.mkv,.webm' });
  const titleInput = h('input', { id: 'title', type: 'text', maxLength: 200, placeholder: '비워 두면 파일 이름을 씁니다' });
  const fileNote = h('p', { class: 'muted' });
  const error = h('p', { class: 'error', role: 'alert', hidden: true });
  const stage = h('p', { role: 'status' });
  const bar = h('progress', { max: 100, value: 0, hidden: true, 'aria-label': '업로드 진행률' });
  const submit = h('button', { type: 'submit', class: 'button primary' }, '업로드하고 분석 시작');
  const retry = h('button', { type: 'button', class: 'button', hidden: true }, '다시 시도');

  let created = null; // 작업을 만든 뒤 업로드만 실패하면 같은 작업으로 다시 올린다.
  let uploaded = false;
  let busy = false;
  let upload = null;
  let gone = false;

  const showError = (msg) => {
    error.textContent = msg;
    error.hidden = !msg;
  };

  fileInput.addEventListener('change', () => {
    const f = fileInput.files[0];
    showError('');
    fileNote.textContent = '';
    if (!f) return;
    const problem = checkFileSize(f.size);
    if (problem) showError(problem);
    else fileNote.textContent = `선택한 파일: ${f.name} (${formatMB(f.size)} MB)`;
  });

  async function run() {
    const file = fileInput.files[0];
    if (!file) return showError('올릴 영상 파일을 선택해 주세요.');
    const problem = checkFileSize(file.size);
    if (problem) return showError(problem);
    if (busy) return;
    busy = true;
    showError('');
    retry.hidden = true;
    submit.disabled = true;
    fileInput.disabled = true;
    titleInput.disabled = true;
    try {
      if (!created) {
        stage.textContent = '작업을 만드는 중…';
        const body = { fileName: file.name, size: file.size, contentType: file.type || 'application/octet-stream' };
        if (titleInput.value.trim()) body.title = titleInput.value.trim();
        created = await createJob(body);
      }
      if (!uploaded) {
        stage.textContent = '영상을 올리는 중…';
        bar.hidden = false;
        upload = uploadFile(created.uploadUrl, created.uploadHeaders, file, (loaded, total) => {
          bar.value = total ? (loaded / total) * 100 : 0;
          stage.textContent = `영상을 올리는 중… ${formatProgress(loaded, total)}`;
        });
        await upload.promise;
        uploaded = true;
      }
      stage.textContent = '분석을 시작하는 중…';
      await startJob(created.jobId);
      if (!gone) location.hash = `#/jobs/${encodeURIComponent(created.jobId)}`;
    } catch (e) {
      if (gone) return;
      stage.textContent = '';
      showError(e.message);
      retry.hidden = false;
    } finally {
      busy = false;
    }
  }

  retry.addEventListener('click', run);
  const form = h('form', { class: 'card', onsubmit: (e) => { e.preventDefault(); run(); } },
    h('div', { class: 'field' }, h('label', { for: 'file' }, '영상 파일 (최대 5 GiB)'), fileInput, fileNote),
    h('div', { class: 'field' }, h('label', { for: 'title' }, '작업 이름 (선택)'), titleInput),
    h('div', { class: 'actions' }, submit, retry, h('a', { class: 'button', href: '#/' }, '취소')),
    stage, bar, error);
  root.append(h('h1', {}, '새 작업'), form);

  return () => {
    gone = true;
    if (upload && !uploaded) upload.abort();
  };
}
