import { h } from '../dom.js';

export function loginView(root, { error } = {}) {
  let message = null;
  if (error === 'not_member') {
    message = 'GitHub awskrug 조직의 sns-working-group 팀 멤버만 로그인할 수 있습니다. 팀에 속해 있는데도 이 메시지가 보이면 팀 관리자에게 멤버 등록을 요청해 주세요.';
  } else if (error) {
    message = '로그인에 실패했습니다. 잠시 뒤에 다시 시도해 주세요.';
  }
  root.append(
    h('section', { class: 'card narrow' },
      h('h1', {}, '로그인'),
      h('p', {}, 'AWSKRUG 밋업 영상에서 쇼츠를 만드는 도구입니다. GitHub 계정으로 로그인하세요.'),
      message ? h('p', { class: 'error', role: 'alert' }, message) : null,
      h('a', { class: 'button primary', href: '/api/auth/login' }, 'GitHub로 로그인'),
    ),
  );
}
