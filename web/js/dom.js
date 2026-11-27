// DOM 생성 helper. 문자열 자식은 항상 텍스트 노드가 되므로 서버가 준 문자열도 안전하다.

export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props ?? {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k in node) node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c != null && c !== false) node.append(c);
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
}

// 주기 실행. fn이 true를 돌려주면 ms 뒤에 다시 부른다. 예외는 삼키고 계속한다(fn이 직접 표시한다).
export function poll(fn, ms = 5000) {
  let stopped = false;
  let timer = 0;
  const tick = async () => {
    let again = true;
    try {
      again = await fn();
    } catch {
      again = true;
    }
    clearTimeout(timer);
    if (!stopped && again) timer = setTimeout(tick, ms);
  };
  tick();
  return {
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
    refresh() {
      clearTimeout(timer);
      if (!stopped) tick();
    },
    get stopped() {
      return stopped;
    },
  };
}
