// XMLHttpRequest PUT 업로드(진행률을 얻기 위해 fetch 대신 사용).

const FORBIDDEN = new Set(['host', 'content-length']);

export function uploadFile(url, headers, file, onProgress) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open('PUT', url);
    for (const [k, v] of Object.entries(headers ?? {})) {
      if (!FORBIDDEN.has(k.toLowerCase())) xhr.setRequestHeader(k, v);
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`업로드에 실패했습니다 (HTTP ${xhr.status}).`));
    xhr.onerror = () => reject(new Error('업로드 중 네트워크 오류가 발생했습니다.'));
    xhr.onabort = () => reject(new Error('업로드가 취소되었습니다.'));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}
