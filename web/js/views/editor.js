import { h } from '../dom.js';
import { createRender } from '../api.js';
import {
  formatTime, validVideoSize, fitContain, sourceToDisplay, moveRect, resizeRect, minSourceSize, buildRenderBody, defaultRegions,
} from '../geometry.js';
import { safeUrl } from '../status.js';

const REGIONS = [
  { key: 'slide', label: '발표 화면' },
  { key: 'speaker', label: '발표자' },
];
const HANDLES = ['nw', 'ne', 'sw', 'se'];
const ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

// 후보 구간 하나에 대한 영역 편집기. {element, destroy}를 돌려준다.
export function createEditor({ job, jobId, index, onRequested }) {
  const cand = job.candidates[index];
  const vid = { w: job.width, h: job.height };
  if (!validVideoSize(vid)) {
    return {
      element: h('section', { class: 'card editor', 'aria-label': '영역 지정' },
        h('h2', {}, `영역 지정: ${cand.title}`),
        h('p', { class: 'error', role: 'alert' }, '영상 크기 정보가 없어 영역을 지정할 수 없습니다. 분석을 다시 실행해 주세요.')),
      destroy() {},
    };
  }
  const min = minSourceSize(vid);
  const rects = defaultRegions(vid); // 원본 픽셀 기준 {slide, speaker}
  let content = { x: 0, y: 0, w: 1, h: 1 };

  const video = h('video', { class: 'stage-video', src: safeUrl(job.sourceUrl), preload: 'metadata', playsInline: true });
  const overlay = h('div', { class: 'overlay' });
  const stage = h('div', { class: 'stage', style: `aspect-ratio: ${vid.w} / ${vid.h}` }, video, overlay);

  function place(key) {
    const d = sourceToDisplay(rects[key], content, vid);
    const s = regionEls[key].style;
    s.left = `${d.x}px`;
    s.top = `${d.y}px`;
    s.width = `${d.w}px`;
    s.height = `${d.h}px`;
  }
  function layout() {
    if (!stage.clientWidth || !stage.clientHeight) return;
    content = fitContain(stage.clientWidth, stage.clientHeight, vid.w, vid.h);
    REGIONS.forEach((r) => place(r.key));
  }

  const regionEls = {};
  for (const { key, label } of REGIONS) {
    const el = h('div', {
      class: `region region-${key}`,
      tabIndex: 0,
      role: 'group',
      'aria-label': `${label} 영역. 방향키로 이동하고 Shift와 방향키로 크기를 바꿉니다.`,
    },
    h('span', { class: 'region-label' }, label),
    HANDLES.map((hd) => h('span', { class: `handle handle-${hd}`, 'data-handle': hd })));
    regionEls[key] = el;
    overlay.append(el);

    let drag = null;
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      el.focus();
      drag = { handle: e.target.dataset?.handle ?? null, x: e.clientX, y: e.clientY, start: { ...rects[key] } };
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = ((e.clientX - drag.x) * vid.w) / content.w;
      const dy = ((e.clientY - drag.y) * vid.h) / content.h;
      rects[key] = drag.handle
        ? resizeRect(drag.start, drag.handle, dx, dy, vid, min)
        : moveRect(drag.start, dx, dy, vid);
      place(key);
    });
    const end = () => { drag = null; };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('keydown', (e) => {
      const a = ARROWS[e.key];
      if (!a) return;
      e.preventDefault();
      const step = Math.max(1, Math.round(vid.w / 100));
      rects[key] = e.shiftKey
        ? resizeRect(rects[key], 'se', a[0] * step, a[1] * step, vid, min)
        : moveRect(rects[key], a[0] * step, a[1] * step, vid);
      place(key);
    });
  }

  // 구간 재생 제어: 시작과 끝 사이만 재생하고 끝에서 멈춘다.
  const playBtn = h('button', { type: 'button', class: 'button' }, '재생');
  const scrub = h('input', { type: 'range', min: cand.start, max: cand.end, step: 0.1, value: cand.start, 'aria-label': '구간 위치' });
  const timeText = h('span', { class: 'time', 'aria-live': 'off' });
  const updateTime = () => {
    timeText.textContent = `${formatTime(video.currentTime - cand.start)} / ${formatTime(cand.end - cand.start)}`;
    scrub.value = video.currentTime;
  };
  video.addEventListener('loadedmetadata', () => { video.currentTime = cand.start; });
  video.addEventListener('timeupdate', () => {
    if (video.currentTime >= cand.end) {
      video.pause();
      video.currentTime = cand.end;
    }
    updateTime();
  });
  video.addEventListener('play', () => { playBtn.textContent = '일시정지'; });
  video.addEventListener('pause', () => { playBtn.textContent = '재생'; });
  playBtn.addEventListener('click', () => {
    if (!video.paused) return video.pause();
    if (video.currentTime >= cand.end - 0.05 || video.currentTime < cand.start) video.currentTime = cand.start;
    video.play().catch(() => {});
  });
  scrub.addEventListener('input', () => { video.currentTime = Number(scrub.value); });
  updateTime();

  const noRegions = h('input', { id: 'no-regions', type: 'checkbox' });
  noRegions.addEventListener('change', () => {
    overlay.hidden = noRegions.checked;
  });
  const message = h('p', { role: 'status' });
  const submit = h('button', { type: 'button', class: 'button primary' }, '렌더링 요청');
  submit.addEventListener('click', async () => {
    submit.disabled = true;
    message.className = '';
    message.textContent = '요청하는 중…';
    try {
      const body = buildRenderBody({ index, noRegions: noRegions.checked, slide: rects.slide, speaker: rects.speaker, vid });
      await createRender(jobId, body);
      message.textContent = '렌더링을 요청했습니다. 아래 렌더링 결과에서 진행 상태를 확인하세요.';
      onRequested();
    } catch (e) {
      message.className = 'error';
      message.textContent = e.message;
    } finally {
      submit.disabled = false;
    }
  });

  const element = h('section', { class: 'card editor', 'aria-label': '영역 지정' },
    h('h2', {}, `영역 지정: ${cand.title}`),
    h('p', { class: 'hint' },
      '이 구간 안에서 카메라가 움직일 수 있습니다. 발표 화면과 발표자가 구간 내내 사각형 안에 들어오도록 넉넉하게 잡아 주세요. 영상을 재생해 시작과 끝 모습을 모두 확인하는 것이 좋습니다.'),
    stage,
    h('div', { class: 'player-controls' }, playBtn, scrub, timeText),
    h('p', { class: 'legend' },
      h('span', { class: 'swatch swatch-slide' }), '발표 화면 (실선 파랑)  ',
      h('span', { class: 'swatch swatch-speaker' }), '발표자 (점선 주황)'),
    h('div', { class: 'field inline' }, noRegions, h('label', { for: 'no-regions' }, '영역 없이 렌더링(2번, 3번 구성만)')),
    h('div', { class: 'actions' }, submit),
    message);

  const observer = new ResizeObserver(layout);
  observer.observe(stage);
  queueMicrotask(layout);

  return {
    element,
    destroy() {
      observer.disconnect();
      video.pause();
      video.removeAttribute('src');
      video.load();
    },
  };
}
