// 영역 지정에 쓰는 순수 함수 모음. DOM에 의존하지 않는다.
// 사각형 {x, y, w, h}의 단위는 문맥에 따라 화면 픽셀(display) 또는 원본 영상 픽셀(source)이다.

// 영상 크기가 유한한 양수인지 확인한다. 아니면 영역 좌표를 계산할 수 없다.
export function validVideoSize(vid) {
  return Number.isFinite(vid?.w) && Number.isFinite(vid?.h) && vid.w > 0 && vid.h > 0;
}

export function formatTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export function clamp(v, lo, hi) {
  return Math.min(Math.max(v, lo), hi);
}

// object-fit: contain 으로 그려진 영상이 상자(boxW x boxH) 안에서 차지하는 영역.
export function fitContain(boxW, boxH, vidW, vidH) {
  const scale = Math.min(boxW / vidW, boxH / vidH);
  const w = vidW * scale;
  const h = vidH * scale;
  return { x: (boxW - w) / 2, y: (boxH - h) / 2, w, h };
}

// vid: {w, h}. 사각형을 [0, vid.w] x [0, vid.h] 안으로 넣고 최소 크기를 보장한다.
export function clampRect(r, vid, minSize = 1) {
  const w = clamp(r.w, Math.min(minSize, vid.w), vid.w);
  const h = clamp(r.h, Math.min(minSize, vid.h), vid.h);
  return { x: clamp(r.x, 0, vid.w - w), y: clamp(r.y, 0, vid.h - h), w, h };
}

// content: fitContain 결과(화면 안의 영상 영역). 상자 좌표를 영상 영역 기준으로 환산한다.
export function sourceToDisplay(r, content, vid) {
  const sx = content.w / vid.w;
  const sy = content.h / vid.h;
  return { x: content.x + r.x * sx, y: content.y + r.y * sy, w: r.w * sx, h: r.h * sy };
}

export function displayToSource(r, content, vid) {
  const sx = vid.w / content.w;
  const sy = vid.h / content.h;
  return { x: (r.x - content.x) * sx, y: (r.y - content.y) * sy, w: r.w * sx, h: r.h * sy };
}

export function minSourceSize(vid) {
  return Math.max(32, Math.round(Math.min(vid.w, vid.h) * 0.05));
}

export function moveRect(r, dx, dy, vid) {
  return clampRect({ ...r, x: r.x + dx, y: r.y + dy }, vid, 1);
}

// handle: 'nw' | 'ne' | 'sw' | 'se' (또는 'n', 'e' 등 한 글자). 반대편 모서리는 고정된다.
export function resizeRect(r, handle, dx, dy, vid, min) {
  let x1 = r.x;
  let y1 = r.y;
  let x2 = r.x + r.w;
  let y2 = r.y + r.h;
  if (handle.includes('w')) x1 = clamp(x1 + dx, 0, x2 - min);
  if (handle.includes('e')) x2 = clamp(x2 + dx, x1 + min, vid.w);
  if (handle.includes('n')) y1 = clamp(y1 + dy, 0, y2 - min);
  if (handle.includes('s')) y2 = clamp(y2 + dy, y1 + min, vid.h);
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

// API로 보내는 [x, y, w, h]. 정수이며 영상 범위 안에 있고 폭과 높이는 1 이상이다.
export function toApiRect(r, vid) {
  const c = clampRect(r, vid, 1);
  const x = Math.min(Math.round(c.x), vid.w - 1);
  const y = Math.min(Math.round(c.y), vid.h - 1);
  const w = Math.max(1, Math.min(Math.round(c.w), vid.w - x));
  const h = Math.max(1, Math.min(Math.round(c.h), vid.h - y));
  return [x, y, w, h];
}

// 처음에 보여 주는 두 사각형(원본 픽셀). 왼쪽에 발표 화면, 오른쪽에 발표자를 놓는다.
export function defaultRegions(vid) {
  return {
    slide: { x: vid.w * 0.05, y: vid.h * 0.1, w: vid.w * 0.58, h: vid.h * 0.8 },
    speaker: { x: vid.w * 0.68, y: vid.h * 0.15, w: vid.w * 0.27, h: vid.h * 0.7 },
  };
}

// POST /api/jobs/{jobId}/renders 본문. noRegions가 켜져 있으면 slide와 speaker는 둘 다 null이다.
export function buildRenderBody({ index, noRegions, slide, speaker, vid }) {
  if (!noRegions && !validVideoSize(vid)) throw new Error('영상 크기를 알 수 없어 영역을 지정할 수 없습니다.');
  return {
    index,
    slide: noRegions ? null : toApiRect(slide, vid),
    speaker: noRegions ? null : toApiRect(speaker, vid),
  };
}
