import test from 'node:test';
import assert from 'node:assert/strict';
import {
  formatTime, fitContain, clampRect, sourceToDisplay, displayToSource, toApiRect,
  moveRect, resizeRect, minSourceSize, buildRenderBody, defaultRegions,
} from '../js/geometry.js';

const vid = { w: 1920, h: 1080 };
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test('formatTime: mm:ss, 내림, 음수와 비정상 값은 00:00', () => {
  assert.equal(formatTime(0), '00:00');
  assert.equal(formatTime(65.9), '01:05');
  assert.equal(formatTime(600), '10:00');
  assert.equal(formatTime(3725), '62:05');
  assert.equal(formatTime(-3), '00:00');
  assert.equal(formatTime(undefined), '00:00');
});

test('fitContain: 16:9 영상이 더 넓은 상자에서는 좌우에 여백', () => {
  const c = fitContain(1600, 450, 1920, 1080); // 상자 비율 약 3.56
  near(c.h, 450);
  near(c.w, 800);
  near(c.x, 400);
  near(c.y, 0);
});

test('fitContain: 16:9 영상이 더 높은 상자에서는 위아래에 여백', () => {
  const c = fitContain(800, 800, 1920, 1080);
  near(c.w, 800);
  near(c.h, 450);
  near(c.x, 0);
  near(c.y, 175);
});

test('fitContain: 비율이 같으면 여백 없음', () => {
  assert.deepEqual(fitContain(960, 540, 1920, 1080), { x: 0, y: 0, w: 960, h: 540 });
});

test('sourceToDisplay와 displayToSource는 서로 역변환(레터박스 포함)', () => {
  for (const box of [[1600, 450], [800, 800], [960, 540]]) {
    const content = fitContain(box[0], box[1], vid.w, vid.h);
    const src = { x: 120, y: 80, w: 900, h: 500 };
    const back = displayToSource(sourceToDisplay(src, content, vid), content, vid);
    for (const k of ['x', 'y', 'w', 'h']) near(back[k], src[k], 1e-6);
  }
});

test('sourceToDisplay: 위아래 여백만큼 y가 내려간다', () => {
  const content = fitContain(800, 800, vid.w, vid.h); // y 여백 175, 배율 0.41666
  const d = sourceToDisplay({ x: 0, y: 0, w: 1920, h: 1080 }, content, vid);
  assert.deepEqual(d, { x: 0, y: 175, w: 800, h: 450 });
});

test('displayToSource: 영상 영역 안의 화면 좌표를 원본 픽셀로 바꾼다', () => {
  const content = fitContain(1600, 450, vid.w, vid.h); // x 여백 400, 배율 800/1920
  const s = displayToSource({ x: 400, y: 0, w: 400, h: 225 }, content, vid);
  near(s.x, 0);
  near(s.w, 960);
  near(s.h, 540);
});

test('clampRect: 범위 밖 사각형을 안으로 넣고 크기를 제한', () => {
  assert.deepEqual(clampRect({ x: -50, y: 1000, w: 300, h: 300 }, vid), { x: 0, y: 780, w: 300, h: 300 });
  assert.deepEqual(clampRect({ x: 0, y: 0, w: 5000, h: 5000 }, vid), { x: 0, y: 0, w: 1920, h: 1080 });
});

test('clampRect: 최소 크기 보장', () => {
  const r = clampRect({ x: 10, y: 10, w: 2, h: 3 }, vid, 40);
  assert.equal(r.w, 40);
  assert.equal(r.h, 40);
});

test('moveRect: 크기는 유지하고 경계에서 멈춘다', () => {
  const r = moveRect({ x: 1500, y: 100, w: 400, h: 300 }, 500, -500, vid);
  assert.deepEqual(r, { x: 1520, y: 0, w: 400, h: 300 });
});

test('resizeRect: 반대편 모서리는 고정, 최소 크기와 경계 적용', () => {
  const r = { x: 100, y: 100, w: 400, h: 300 };
  assert.deepEqual(resizeRect(r, 'se', 50, 20, vid, 40), { x: 100, y: 100, w: 450, h: 320 });
  assert.deepEqual(resizeRect(r, 'nw', 1000, 1000, vid, 40), { x: 460, y: 360, w: 40, h: 40 });
  assert.deepEqual(resizeRect(r, 'se', 9999, 9999, vid, 40), { x: 100, y: 100, w: 1820, h: 980 });
  assert.deepEqual(resizeRect(r, 'nw', -999, -999, vid, 40), { x: 0, y: 0, w: 500, h: 400 });
});

test('toApiRect: 정수이며 영상 범위 안', () => {
  const a = toApiRect({ x: 10.4, y: 20.6, w: 100.5, h: 50.49 }, vid);
  assert.ok(a.every(Number.isInteger));
  assert.deepEqual(a, [10, 21, 101, 50]);
  const b = toApiRect({ x: 1900, y: 1070, w: 500, h: 500 }, vid);
  assert.ok(b[0] + b[2] <= vid.w && b[1] + b[3] <= vid.h);
  assert.ok(b[2] >= 1 && b[3] >= 1);
});

test('화면 -> 원본 -> API 왕복에서 오차는 1픽셀 이내', () => {
  const content = fitContain(1000, 700, vid.w, vid.h);
  const src = { x: 333, y: 222, w: 777, h: 444 };
  const api = toApiRect(displayToSource(sourceToDisplay(src, content, vid), content, vid), vid);
  [src.x, src.y, src.w, src.h].forEach((v, i) => assert.ok(Math.abs(api[i] - v) <= 1));
});

test('minSourceSize와 defaultRegions', () => {
  assert.equal(minSourceSize(vid), 54);
  assert.equal(minSourceSize({ w: 320, h: 240 }), 32);
  const d = defaultRegions(vid);
  for (const r of [d.slide, d.speaker]) {
    assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.w <= vid.w && r.y + r.h <= vid.h);
  }
});

test('buildRenderBody: 체크박스가 켜지면 slide와 speaker는 둘 다 null', () => {
  const slide = { x: 10, y: 10, w: 100, h: 100 };
  const speaker = { x: 200, y: 10, w: 100, h: 100 };
  assert.deepEqual(buildRenderBody({ index: 2, noRegions: true, slide, speaker, vid }), { index: 2, slide: null, speaker: null });
  assert.deepEqual(buildRenderBody({ index: 0, noRegions: false, slide, speaker, vid }), {
    index: 0, slide: [10, 10, 100, 100], speaker: [200, 10, 100, 100],
  });
});

import { validVideoSize, buildRenderBody as _brb } from '../js/geometry.js';
import { safeUrl as _safeUrl, checkFileSize as _cfs } from '../js/status.js';

test('영상 크기가 없거나 0이면 영역 요청을 만들지 않는다', () => {
  for (const vid of [{ w: undefined, h: undefined }, { w: 0, h: 1080 }, { w: 1920, h: NaN }, { w: Infinity, h: 10 }]) {
    assert.equal(validVideoSize(vid), false);
    const r = { x: 0, y: 0, w: 10, h: 10 };
    assert.throws(() => _brb({ index: 0, noRegions: false, slide: r, speaker: r, vid }), /영상 크기/);
  }
  assert.deepEqual(_brb({ index: 1, noRegions: true, vid: { w: 0, h: 0 } }), { index: 1, slide: null, speaker: null });
});

test('safeUrl은 프로토콜 상대 주소와 javascript: 를 거부한다', () => {
  for (const u of ['//evil.example/x', '/\\evil.example', 'javascript:alert(1)', 'data:text/html,x', ' https://a', undefined]) assert.equal(_safeUrl(u), '');
  assert.equal(_safeUrl('/dev-sample/a.mp4'), '/dev-sample/a.mp4');
  assert.equal(_safeUrl('https://b.s3.amazonaws.com/k?X=1'), 'https://b.s3.amazonaws.com/k?X=1');
});

test('5 GiB 경계: 정확히 5 GiB는 허용, 1바이트 초과는 거부', () => {
  assert.equal(_cfs(5368709120), null);
  assert.ok(_cfs(5368709121));
});
