// 상태 문구와 업로드 검사 등 화면에 쓰는 순수 helper.

export const JOB_STATUS_LABELS = {
  UPLOADING: '업로드 중',
  QUEUED: '대기 중',
  EXTRACTING: '음성 추출 중',
  TRANSCRIBING: '전사 중',
  SELECTING: '구간 후보 선정 중',
  READY: '분석 완료',
  FAILED: '실패',
};

export const RENDER_STATUS_LABELS = {
  RENDERING: '렌더링 중',
  DONE: '완료',
  FAILED: '실패',
};

export const LAYOUTS = [
  { id: 'split', label: '1. 발표 화면 + 발표자 (split)' },
  { id: 'blur', label: '2. 흐린 배경 (blur)' },
  { id: 'crop', label: '3. 가운데 자르기 (crop)' },
];

export const MAX_UPLOAD_BYTES = 5 * 1024 ** 3; // 5 GiB = 5,368,709,120 바이트

export function jobStatusLabel(s) {
  return JOB_STATUS_LABELS[s] ?? `알 수 없음 (${s})`;
}

export function renderStatusLabel(s) {
  return RENDER_STATUS_LABELS[s] ?? `알 수 없음 (${s})`;
}

export function isFinalJobStatus(s) {
  return s === 'READY' || s === 'FAILED';
}

export function hasRendering(job) {
  return Object.values(job?.renders ?? {}).some((r) => r.status === 'RENDERING');
}

// 최신순. renderId를 항목에 합쳐 돌려준다.
export function sortedRenders(job) {
  return Object.entries(job?.renders ?? {})
    .map(([renderId, r]) => ({ renderId, ...r }))
    .sort((a, b) => String(b.createdAt ?? '').localeCompare(String(a.createdAt ?? '')));
}

// 문제가 있으면 한국어 오류 문구, 없으면 null.
export function checkFileSize(size) {
  if (!(size > 0)) return '비어 있는 파일은 올릴 수 없습니다.';
  if (size > MAX_UPLOAD_BYTES) {
    return `파일이 5 GiB를 넘어 올릴 수 없습니다 (선택한 파일: ${(size / 1024 ** 3).toFixed(2)} GiB). 더 작은 파일로 줄여서 다시 시도해 주세요.`;
  }
  return null;
}

export function formatMB(bytes) {
  return (bytes / 1024 ** 2).toFixed(1);
}

export function formatProgress(loaded, total) {
  const pct = total > 0 ? Math.min(100, Math.floor((loaded / total) * 100)) : 0;
  return `${pct}% (${formatMB(loaded)} / ${formatMB(total)} MB)`;
}

export function formatDateTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString('ko-KR');
}

// 서버가 준 주소를 href나 src에 넣기 전에 http(s)와 같은 출처 경로만 허용한다.
export function safeUrl(u) {
  return typeof u === 'string' && (/^https?:\/\//i.test(u) || (u.startsWith('/') && !/^\/[/\\]/.test(u))) ? u : '';
}
