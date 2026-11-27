// 백엔드 없이 화면을 시험하는 개발용 서버. Node 내장 모듈만 쓴다.
//   node dev/mock-server.mjs            (기본 포트 8787, PORT=0 이면 빈 포트)
// web/ 을 정적 파일로 내주고 /api 를 메모리에서 흉내 낸다. 비밀 값은 없다.
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json', '.mp4': 'video/mp4', '.png': 'image/png',
};
const now = () => new Date().toISOString();
const ago = (s) => new Date(Date.now() - s * 1000).toISOString();

const jobs = new Map();
function seed() {
  const jobId = 'a'.repeat(32);
  jobs.set(jobId, {
    jobId, ownerId: '1', ownerLogin: 'mock-user', fileName: 'devops-meetup.mp4', title: 'DevOps 소모임 (예시)',
    status: 'READY', createdAt: ago(3600), updatedAt: ago(3000), width: 1920, height: 1080, duration: 2390,
    candidates: [
      { start: 125, end: 170.5, title: 'IaC로 배포 시간을 줄인 이야기', reason: '구체적인 수치와 함께 핵심 경험을 설명합니다.' },
      { start: 1210, end: 1261, title: '장애 대응에서 배운 점', reason: '청중에게 바로 도움이 되는 조언을 담고 있습니다.' },
    ],
    renders: {
      [`r${'1'.repeat(15)}`]: {
        index: 0, slide: [100, 100, 1100, 700], speaker: [1300, 200, 500, 700], status: 'DONE', createdAt: ago(1800),
        outputs: ['split', 'blur', 'crop'].map((layout) => ({ layout, key: `x/${layout}.mp4`, url: `/dev-sample/${layout}.mp4` })),
      },
    },
  });
}
seed();

// 작업이 시간에 따라 진행되는 모습을 흉내 낸다.
function advance(job, steps) {
  steps.forEach(([ms, fn]) => setTimeout(() => { fn(job); job.updatedAt = now(); }, ms).unref());
}

function view(job) {
  const out = structuredClone(job);
  out.sourceUrl = '/dev-sample/source.mp4';
  for (const r of Object.values(out.renders)) {
    for (const o of r.outputs ?? []) o.url = `/dev-sample/${o.layout}.mp4`;
  }
  return out;
}

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { 'Content-Type': 'application/json', ...headers });
  res.end(body === undefined ? undefined : JSON.stringify(body));
};
const readBody = async (req) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const text = Buffer.concat(chunks).toString();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    return {};
  }
};
function safeDecode(s) {
  try {
    return decodeURIComponent(s);
  } catch {
    return '\0';
  }
}
const loggedIn = (req) => /(^|;\s*)session=mock/.test(req.headers.cookie ?? '');

async function api(req, res, url) {
  const path = url.pathname;
  if (path === '/api/auth/login') {
    const denied = url.searchParams.get('deny') === '1';
    res.writeHead(302, { Location: denied ? '/?error=not_member' : '/', ...(denied ? {} : { 'Set-Cookie': 'session=mock; Path=/; HttpOnly; SameSite=Lax' }) });
    return res.end();
  }
  if (!loggedIn(req)) return send(res, 401, { error: '로그인이 필요합니다.' });
  if (path === '/api/auth/logout' && req.method === 'POST') {
    res.writeHead(204, { 'Set-Cookie': 'session=; Path=/; Max-Age=0' });
    return res.end();
  }
  if (path === '/api/me') return send(res, 200, { githubId: '1', login: 'mock-user', name: 'Mock User', avatarUrl: '' });
  if (path === '/api/jobs' && req.method === 'GET') {
    const list = [...jobs.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map(({ jobId, title, fileName, status, ownerLogin, createdAt }) => ({ jobId, title, fileName, status, ownerLogin, createdAt }));
    return send(res, 200, { jobs: list });
  }
  if (path === '/api/jobs' && req.method === 'POST') {
    const b = await readBody(req);
    if (!b.fileName || !(b.size > 0)) return send(res, 400, { error: 'fileName과 size가 필요합니다.' });
    if (b.size > 5368709120) return send(res, 400, { error: '파일이 5 GiB를 넘습니다.' });
    const jobId = randomBytes(16).toString('hex');
    jobs.set(jobId, {
      jobId, ownerId: '1', ownerLogin: 'mock-user', fileName: b.fileName, title: b.title || b.fileName, status: 'UPLOADING',
      createdAt: now(), updatedAt: now(), renders: {},
    });
    return send(res, 201, { jobId, uploadUrl: `/mock-upload/${jobId}`, uploadHeaders: { 'Content-Type': b.contentType || 'application/octet-stream' } });
  }
  const m = path.match(/^\/api\/jobs\/([0-9a-f]{32})(\/start|\/renders)?$/);
  if (!m) return send(res, 404, { error: '없는 경로입니다.' });
  const job = jobs.get(m[1]);
  if (!job) return send(res, 404, { error: '없는 작업입니다.' });
  if (!m[2] && req.method === 'GET') return send(res, 200, view(job));
  if (m[2] === '/start' && req.method === 'POST') {
    if (job.status !== 'UPLOADING') return send(res, 409, { error: '업로드 중인 작업만 시작할 수 있습니다.' });
    job.status = 'QUEUED';
    advance(job, [
      [1500, (j) => { j.status = 'EXTRACTING'; j.width = 1920; j.height = 1080; j.duration = 600; }],
      [4000, (j) => { j.status = 'TRANSCRIBING'; }],
      [7000, (j) => { j.status = 'SELECTING'; }],
      [9000, (j) => {
        j.status = 'READY';
        j.candidates = [{ start: 20, end: 62, title: '예시 구간 1', reason: '가짜 서버가 만든 후보입니다.' }];
      }],
    ]);
    return send(res, 202, { jobId: job.jobId, status: 'QUEUED' });
  }
  if (m[2] === '/renders' && req.method === 'POST') {
    const b = await readBody(req);
    if (job.status !== 'READY') return send(res, 409, { error: '분석이 끝난 작업만 렌더링할 수 있습니다.' });
    if (!Number.isInteger(b.index) || b.index < 0 || b.index >= job.candidates.length) return send(res, 400, { error: 'index가 후보 범위를 벗어났습니다.' });
    const okRect = (r) => Array.isArray(r) && r.length === 4 && r.every(Number.isInteger)
      && r[0] >= 0 && r[1] >= 0 && r[2] > 0 && r[3] > 0 && r[0] + r[2] <= job.width && r[1] + r[3] <= job.height;
    const bothNull = b.slide === null && b.speaker === null;
    if (!bothNull && !(okRect(b.slide) && okRect(b.speaker))) return send(res, 400, { error: 'slide와 speaker는 둘 다 영상 범위 안의 [x, y, w, h]이거나 둘 다 null이어야 합니다.' });
    const renderId = `r${randomBytes(8).toString('hex')}`;
    job.renders[renderId] = { index: b.index, slide: b.slide, speaker: b.speaker, status: 'RENDERING', createdAt: now() };
    setTimeout(() => {
      Object.assign(job.renders[renderId], {
        status: 'DONE',
        outputs: ['split', 'blur', 'crop'].map((layout) => ({ layout, key: `x/${layout}.mp4` })),
      });
    }, 6000).unref();
    return send(res, 202, { renderId });
  }
  return send(res, 404, { error: '없는 경로입니다.' });
}

const server = http.createServer(async (req, res) => {
  // DNS rebinding 방지: 로컬 주소로 들어온 요청만 받는다.
  if (!/^(127\.0\.0\.1|localhost)(:\d+)?$/.test(req.headers.host ?? '')) return send(res, 403, { error: 'forbidden host' });
  const url = new URL(req.url, 'http://localhost');
  try {
    if (url.pathname.startsWith('/api/')) return await api(req, res, url);
    if (url.pathname.startsWith('/mock-upload/') && req.method === 'PUT') {
      req.resume();
      await new Promise((r) => req.on('end', r));
      res.writeHead(200);
      return res.end();
    }
    if (url.pathname.startsWith('/dev-sample/')) {
      // 샘플 영상은 web/dev/sample/ 에 직접 넣으면 쓸 수 있다. 없으면 404.
      const file = join(ROOT, 'dev', 'sample', normalize(url.pathname.slice('/dev-sample/'.length)));
      if (!file.startsWith(join(ROOT, 'dev', 'sample') + sep)) return send(res, 404, { error: 'not found' });
      const data = await readFile(file).catch(() => null);
      if (!data) return send(res, 404, { error: '샘플 영상이 없습니다.' });
      res.writeHead(200, { 'Content-Type': 'video/mp4' });
      return res.end(data);
    }
    const rel = url.pathname === '/' ? 'index.html' : safeDecode(url.pathname.slice(1));
    const file = resolve(ROOT, rel);
    if (!file.startsWith(ROOT + sep)) return send(res, 403, { error: 'forbidden' });
    const data = await readFile(file).catch(() => null);
    if (!data) return send(res, 404, { error: 'not found' });
    res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream' });
    return res.end(data);
  } catch (e) {
    return send(res, 500, { error: String(e.message ?? e) });
  }
});

server.listen(Number(process.env.PORT ?? 8787), '127.0.0.1', () => {
  console.log(`mock server: http://127.0.0.1:${server.address().port}/  (로그인 거부 시험: /api/auth/login?deny=1)`);
});
