import test from 'node:test';
import assert from 'node:assert/strict';
import { getJob, createJob, logout, setUnauthorizedHandler, ApiError } from '../js/api.js';

function fakeFetch(status, body) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return { status, ok: status >= 200 && status < 300, text: async () => (body === undefined ? '' : JSON.stringify(body)) };
  };
  return calls;
}

test('오류 본문의 error 문구를 그대로 쓴다', async () => {
  fakeFetch(409, { error: '이미 시작된 작업입니다.' });
  await assert.rejects(getJob('abc'), (e) => e instanceof ApiError && e.status === 409 && e.message === '이미 시작된 작업입니다.');
});

test('본문이 없는 오류는 상태 코드를 알려 준다', async () => {
  fakeFetch(500);
  await assert.rejects(getJob('abc'), /HTTP 500/);
});

test('401이면 로그인 처리기를 부른다', async () => {
  fakeFetch(401, { error: 'x' });
  let called = 0;
  setUnauthorizedHandler(() => { called++; });
  await assert.rejects(getJob('abc'), (e) => e.status === 401);
  assert.equal(called, 1);
});

test('204는 null, 요청 본문은 JSON, 경로는 인코딩', async () => {
  const calls = fakeFetch(204);
  assert.equal(await logout(), null);
  assert.equal(calls[0].init.method, 'POST');
  const calls2 = fakeFetch(201, { jobId: 'j' });
  await createJob({ fileName: 'a.mp4', size: 1, contentType: 'video/mp4' });
  assert.equal(calls2[0].init.body, '{"fileName":"a.mp4","size":1,"contentType":"video/mp4"}');
  await getJob('a/b');
  assert.equal(calls2[1].url, '/api/jobs/a%2Fb');
});

test('네트워크 오류는 한국어 문구', async () => {
  globalThis.fetch = async () => { throw new TypeError('x'); };
  await assert.rejects(getJob('a'), /연결할 수 없습니다/);
});
