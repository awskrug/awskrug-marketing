# awskrug-marketing

AWSKRUG(AWS 한국 사용자 모임)의 밋업 녹화 영상을 마케팅용 영상으로 자동 가공하는 서비스입니다. AWS 위에 자체 구축합니다.

> 현재 상태(2026-11-26): 리포 구조와 설계 초안만 있습니다. 동작하는 기능은 아직 없습니다.

## 만들려는 기능

**1. 슬라이드 동기화 영상.** 운영진이 밋업 녹화 영상과 발표 슬라이드 파일(PPT 또는 PDF)을 함께 업로드합니다. 서비스는 슬라이드를 화면 중앙에 크게 배치하고 녹화 영상을 좌측 하단에 작게 배치한 영상을 만듭니다. 발표자가 다음 슬라이드로 넘어간 시점에 맞춰 화면의 슬라이드도 넘어갑니다.

**2. 쇼츠 자동 생성.** 서비스는 녹화 영상의 내용을 요약하고, 핵심 구간을 골라 YouTube Shorts와 SNS에 올릴 수 있는 세로(9:16) 영상을 만듭니다.

**3. YouTube 자동 업로드.** 서비스는 완성한 영상을 AWSKRUG YouTube 채널에 비공개 상태로 올립니다. 운영진이 내용을 확인한 뒤 공개합니다.

## 디렉토리 구조

| 경로 | 내용 |
|---|---|
| `docs/` | 아키텍처 설계 초안과 참고 프로젝트 검토 결과 |
| `infra/` | AWS CDK(TypeScript) 앱. `cdk init`으로 만든 빈 스택이 들어 있습니다. |
| `services/slide-sync/` | 기능 1(슬라이드 동기화 영상)의 처리 코드가 들어갈 자리 |
| `services/shorts/` | 기능 2(쇼츠 자동 생성)의 처리 코드가 들어갈 자리 |
| `services/common/` | 두 기능이 함께 쓰는 코드(음성 전사, FFmpeg 호출, S3 입출력)가 들어갈 자리 |
| `web/` | 영상과 슬라이드를 업로드하고 결과를 내려받는 화면이 들어갈 자리 |
| `samples/` | 로컬 시험용 입력 파일을 두는 자리. 영상과 슬라이드 파일은 git에 올리지 않습니다. |

## 문서

- [docs/mvp-shorts.md](docs/mvp-shorts.md): 첫 구현 범위(쇼츠 MVP)와 2026-11-26 인터뷰의 결정 사항
- [docs/architecture.md](docs/architecture.md): 처리 흐름과 AWS 서비스 구성 초안, YouTube 업로드의 전제 조건, 아직 결정하지 않은 사항
- [docs/reference-kshorts-studio.md](docs/reference-kshorts-studio.md): 쇼츠 생성에 `lsszz2100/kshorts-studio`를 쓸 수 있는지 검토한 결과

## 개발

```bash
cd infra
npm install
npx cdk synth
```

## 라이선스

[MIT](LICENSE)
