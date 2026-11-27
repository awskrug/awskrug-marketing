import { h, clear } from './dom.js';
import { getMe, logout, setUnauthorizedHandler } from './api.js';
import { safeUrl } from './status.js';
import { loginView } from './views/login.js';
import { jobsView } from './views/jobs.js';
import { newJobView } from './views/newjob.js';
import { jobView } from './views/job.js';

const app = document.getElementById('app');
const userBox = document.getElementById('user');
let me = null;
let cleanup = null;
let loginShown = false;

function show(view, params) {
  if (cleanup) cleanup();
  cleanup = null;
  clear(app);
  loginShown = view === loginView;
  cleanup = view(app, params) ?? null;
}

function showLogin() {
  me = null;
  renderUser();
  if (!loginShown) show(loginView, { error: new URLSearchParams(location.search).get('error') });
}

function renderUser() {
  clear(userBox);
  if (!me) return;
  const avatar = safeUrl(me.avatarUrl);
  userBox.append(
    avatar ? h('img', { class: 'avatar', src: avatar, alt: '', width: 28, height: 28 }) : null,
    h('span', { class: 'login-name' }, me.login),
    h('button', { type: 'button', class: 'button', onclick: onLogout }, '로그아웃'),
  );
}

async function onLogout() {
  try {
    await logout();
  } catch {
    // 401이면 이미 로그인 화면으로 바뀐다. 다른 오류여도 화면은 로그아웃 상태로 둔다.
  }
  showLogin();
}

function route() {
  if (!me) return;
  const hash = location.hash || '#/';
  const job = hash.match(/^#\/jobs\/([^/]+)$/);
  if (hash === '#/new') show(newJobView);
  else if (job) {
    let id;
    try {
      id = decodeURIComponent(job[1]);
    } catch {
      return show(jobsView);
    }
    show(jobView, { id });
  }
  else show(jobsView);
}

async function start() {
  setUnauthorizedHandler(showLogin);
  try {
    me = await getMe();
  } catch (e) {
    if (e.status !== 401) {
      clear(app);
      app.append(
        h('p', { class: 'error', role: 'alert' }, e.message),
        h('button', { type: 'button', class: 'button', onclick: () => location.reload() }, '다시 시도'),
      );
    }
    return;
  }
  renderUser();
  route();
}

window.addEventListener('hashchange', route);
start();
