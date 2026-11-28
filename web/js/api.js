// fetch 래퍼. 같은 출처의 /api 를 부르고 쿠키는 자동으로 간다.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('서버에 연결할 수 없습니다. 네트워크를 확인해 주세요.', 0);
  }
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError('로그인이 필요합니다.', 401);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    throw new ApiError(data?.error || `요청에 실패했습니다 (HTTP ${res.status}).`, res.status);
  }
  return data;
}

const enc = encodeURIComponent;

export const getMe = () => request('GET', '/api/me');
export const logout = () => request('POST', '/api/auth/logout');
export const listJobs = () => request('GET', '/api/jobs');
export const getJob = (id) => request('GET', `/api/jobs/${enc(id)}`);
export const deleteJob = (id) => request('DELETE', `/api/jobs/${enc(id)}`);
export const createJob = (body) => request('POST', '/api/jobs', body);
export const startJob = (id) => request('POST', `/api/jobs/${enc(id)}/start`);
export const createRender = (id, body) => request('POST', `/api/jobs/${enc(id)}/renders`, body);
