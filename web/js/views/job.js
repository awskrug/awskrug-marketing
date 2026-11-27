import { h, clear, poll } from '../dom.js';
import { getJob } from '../api.js';
import { formatTime } from '../geometry.js';
import {
  jobStatusLabel, renderStatusLabel, isFinalJobStatus, hasRendering, sortedRenders, LAYOUTS, safeUrl,
} from '../status.js';
import { createEditor } from './editor.js';

export function jobView(root, { id }) {
  const title = h('h1', {}, '작업');
  const statusLine = h('p', { role: 'status' });
  const error = h('p', { class: 'error', role: 'alert', hidden: true });
  const candidatesBox = h('div');
  const editorBox = h('div');
  const rendersBox = h('section', { 'aria-label': '렌더링 결과' });
  root.append(h('p', {}, h('a', { href: '#/' }, '← 작업 목록')), title, statusLine, error, candidatesBox, editorBox, rendersBox);

  let editor = null;
  let candidatesShown = false;
  let rendersSignature = null;

  function selectCandidate(job, index, button) {
    if (editor) editor.destroy();
    clear(editorBox);
    editor = createEditor({ job, jobId: id, index, onRequested: () => poller.refresh() });
    editorBox.append(editor.element);
    candidatesBox.querySelectorAll('button[aria-pressed]').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    editor.element.scrollIntoView({ block: 'nearest' });
  }

  function showCandidates(job) {
    clear(candidatesBox);
    const list = job.candidates ?? [];
    if (list.length === 0) {
      candidatesBox.append(h('p', { class: 'muted' }, '선정된 구간 후보가 없습니다.'));
      return;
    }
    candidatesBox.append(
      h('h2', {}, '구간 후보'),
      h('p', { class: 'muted' }, '렌더링할 구간을 선택하면 영역 지정 화면이 열립니다.'),
      h('ul', { class: 'candidates' }, list.map((c, i) => {
        const btn = h('button', { type: 'button', class: 'candidate', 'aria-pressed': 'false' },
          h('strong', {}, c.title),
          h('span', { class: 'range' }, `${formatTime(c.start)} - ${formatTime(c.end)} (${Math.round(c.end - c.start)}초)`),
          h('span', { class: 'reason' }, c.reason));
        btn.addEventListener('click', () => selectCandidate(job, i, btn));
        return h('li', {}, btn);
      })));
  }

  function showRenders(job) {
    const renders = sortedRenders(job);
    const signature = JSON.stringify(renders.map((r) => [r.renderId, r.status]));
    if (signature === rendersSignature) return; // 재생 중인 영상이 끊기지 않도록 바뀐 때만 다시 그린다.
    rendersSignature = signature;
    clear(rendersBox);
    if (renders.length === 0) return;
    rendersBox.append(h('h2', {}, '렌더링 결과'), ...renders.map((r) => renderCard(job, r)));
  }

  const poller = poll(async () => {
    let job;
    try {
      job = await getJob(id);
    } catch (e) {
      if (!poller.stopped) {
        error.textContent = e.message;
        error.hidden = false;
      }
      return e.status !== 404;
    }
    if (poller.stopped) return false;
    error.hidden = true;
    title.textContent = job.title || job.fileName || '작업';
    statusLine.replaceChildren(`상태: ${jobStatusLabel(job.status)}`);
    if (job.status === 'FAILED') {
      error.textContent = `분석에 실패했습니다: ${job.error || '원인을 알 수 없습니다.'}`;
      error.hidden = false;
    }
    if (job.status === 'READY' && !candidatesShown) {
      candidatesShown = true;
      showCandidates(job);
    }
    showRenders(job);
    return !isFinalJobStatus(job.status) || hasRendering(job);
  });

  return () => {
    poller.stop();
    if (editor) editor.destroy();
  };
}

function renderCard(job, r) {
  const cand = job.candidates?.[r.index];
  const card = h('article', { class: 'card render' },
    h('h3', {}, cand ? cand.title : `구간 ${r.index + 1}`),
    h('p', {}, h('span', { class: `badge badge-${String(r.status).toLowerCase()}` }, renderStatusLabel(r.status))));
  if (r.status === 'FAILED') {
    card.append(h('p', { class: 'error' }, `렌더링에 실패했습니다: ${r.error || '원인을 알 수 없습니다.'}`));
  }
  if (r.status === 'DONE') {
    card.append(h('div', { class: 'outputs' }, LAYOUTS.map(({ id, label }) => {
      const out = (r.outputs ?? []).find((o) => o.layout === id);
      const url = safeUrl(out?.url);
      return h('figure', {},
        h('figcaption', {}, label),
        url ? h('video', { class: 'short', src: url, controls: true, preload: 'metadata', playsInline: true }) : h('p', { class: 'muted' }, '결과가 없습니다.'),
        url ? h('a', { class: 'button', href: url, download: `${r.renderId}-${id}.mp4` }, '내려받기') : null);
    })));
  }
  return card;
}
