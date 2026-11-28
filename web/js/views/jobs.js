import { h, clear, poll } from '../dom.js';
import { listJobs, deleteJob } from '../api.js';
import { jobStatusLabel, isFinalJobStatus, formatDateTime, canDeleteJob } from '../status.js';

export function jobsView(root, { login } = {}) {
  const error = h('p', { class: 'error', role: 'alert', hidden: true });
  const body = h('div');
  root.append(
    h('div', { class: 'page-head' },
      h('h1', {}, '작업 목록'),
      h('a', { class: 'button primary', href: '#/new' }, '새 작업 만들기')),
    error,
    body,
  );

  let lastSignature = null;
  const poller = poll(async () => {
    let jobs;
    try {
      jobs = (await listJobs()).jobs ?? [];
    } catch (e) {
      if (!poller.stopped) {
        error.textContent = e.message;
        error.hidden = false;
      }
      return true;
    }
    if (poller.stopped) return false;
    error.hidden = true;
    const signature = JSON.stringify(jobs);
    if (signature !== lastSignature) {
      lastSignature = signature;
      renderTable(body, jobs, login, onDelete);
    }
    return jobs.some((j) => !isFinalJobStatus(j.status));
  });
  async function onDelete(job, button) {
    const name = job.title || job.fileName;
    if (!confirm(`"${name}" 작업을 지울까요? 올린 영상과 만든 쇼츠도 함께 지워지며 되돌릴 수 없습니다.`)) return;
    button.disabled = true;
    try {
      await deleteJob(job.jobId);
    } catch (e) {
      button.disabled = false;
      error.textContent = e.message;
      error.hidden = false;
      return;
    }
    error.hidden = true;
    lastSignature = null;
    poller.refresh();
    button.closest('tr')?.remove();
  }
  return poller.stop;
}

function renderTable(container, jobs, login, onDelete) {
  clear(container);
  if (jobs.length === 0) {
    container.append(h('p', { class: 'muted' }, '아직 작업이 없습니다. 새 작업을 만들어 영상을 올려 보세요.'));
    return;
  }
  const rows = jobs.map((j) =>
    h('tr', {},
      h('td', {}, h('a', { href: `#/jobs/${encodeURIComponent(j.jobId)}` }, j.title || j.fileName)),
      h('td', {}, j.fileName),
      h('td', {}, h('span', { class: `badge badge-${String(j.status).toLowerCase()}` }, jobStatusLabel(j.status))),
      h('td', {}, j.ownerLogin),
      h('td', {}, formatDateTime(j.createdAt)),
      h('td', {}, canDeleteJob(j, login)
        ? h('button', { type: 'button', class: 'button danger', onclick: (e) => onDelete(j, e.currentTarget) }, '삭제')
        : null)));
  container.append(
    h('div', { class: 'table-wrap' },
      h('table', {},
        h('thead', {}, h('tr', {}, ['제목', '파일 이름', '상태', '만든 사람', '만든 시각', ''].map((t) => h('th', { scope: 'col' }, t)))),
        h('tbody', {}, rows))));
}
