import test from 'node:test';
import assert from 'node:assert/strict';
import {
  jobStatusLabel, renderStatusLabel, isFinalJobStatus, hasRendering, sortedRenders,
  checkFileSize, MAX_UPLOAD_BYTES, formatProgress, formatMB, safeUrl,
} from '../js/status.js';

test('작업 상태 문구', () => {
  assert.equal(jobStatusLabel('UPLOADING'), '업로드 중');
  assert.equal(jobStatusLabel('QUEUED'), '대기 중');
  assert.equal(jobStatusLabel('EXTRACTING'), '음성 추출 중');
  assert.equal(jobStatusLabel('TRANSCRIBING'), '전사 중');
  assert.equal(jobStatusLabel('SELECTING'), '구간 후보 선정 중');
  assert.equal(jobStatusLabel('READY'), '분석 완료');
  assert.equal(jobStatusLabel('FAILED'), '실패');
  assert.match(jobStatusLabel('???'), /알 수 없음/);
});

test('렌더링 상태 문구', () => {
  assert.equal(renderStatusLabel('RENDERING'), '렌더링 중');
  assert.equal(renderStatusLabel('DONE'), '완료');
  assert.equal(renderStatusLabel('FAILED'), '실패');
});

test('최종 상태는 READY와 FAILED뿐', () => {
  assert.equal(isFinalJobStatus('READY'), true);
  assert.equal(isFinalJobStatus('FAILED'), true);
  for (const s of ['UPLOADING', 'QUEUED', 'EXTRACTING', 'TRANSCRIBING', 'SELECTING']) {
    assert.equal(isFinalJobStatus(s), false);
  }
});

test('렌더링 진행 여부와 최신순 정렬', () => {
  const job = { renders: {
    a: { status: 'DONE', createdAt: '2026-11-27T01:00:00Z' },
    b: { status: 'RENDERING', createdAt: '2026-11-27T02:00:00Z' },
  } };
  assert.equal(hasRendering(job), true);
  assert.equal(hasRendering({}), false);
  assert.deepEqual(sortedRenders(job).map((r) => r.renderId), ['b', 'a']);
  assert.deepEqual(sortedRenders({}), []);
});

test('5 GiB 검사', () => {
  assert.equal(MAX_UPLOAD_BYTES, 5368709120);
  assert.equal(checkFileSize(MAX_UPLOAD_BYTES), null);
  assert.equal(checkFileSize(1), null);
  assert.match(checkFileSize(MAX_UPLOAD_BYTES + 1), /5 GiB/);
  assert.ok(checkFileSize(0));
  assert.ok(checkFileSize(NaN));
});

test('진행률 문구', () => {
  assert.equal(formatMB(1048576), '1.0');
  assert.equal(formatProgress(5242880, 10485760), '50% (5.0 / 10.0 MB)');
  assert.equal(formatProgress(0, 0), '0% (0.0 / 0.0 MB)');
});

test('safeUrl은 http(s)와 절대 경로만 허용', () => {
  assert.equal(safeUrl('https://x.test/a?b=1'), 'https://x.test/a?b=1');
  assert.equal(safeUrl('/dev/a.mp4'), '/dev/a.mp4');
  assert.equal(safeUrl('javascript:alert(1)'), '');
  assert.equal(safeUrl(undefined), '');
});
