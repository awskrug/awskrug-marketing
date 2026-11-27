# awskrug-marketing

AWSKRUG(AWS 한국 사용자 모임)의 밋업 녹화 영상을 마케팅용 영상으로 자동 가공하는 서비스입니다. AWS 위에 자체 구축합니다.

> 현재 상태(2026-11-27): 기능 2(쇼츠 자동 생성)의 처리 코드, API, 웹 화면, 인프라 정의를 구현했고 테스트와 `cdk synth`가 통과합니다. AWS에는 아직 배포하지 않았으므로 서비스로는 동작하지 않습니다. 처리 코드는 로컬 명령으로 실제 밋업 영상 1편에 대해 전사, 구간 후보 선정, 렌더링까지 실행해 확인했습니다. 배포 전에 담당자가 확인할 작업은 [docs/plans/pending-confirmations.md](docs/plans/pending-confirmations.md)에 있습니다. 기능 1과 기능 3은 아직 구현하지 않았습니다.

## 만들려는 기능

**1. 슬라이드 동기화 영상.** 운영진이 밋업 녹화 영상과 발표 슬라이드 파일(PPT 또는 PDF)을 함께 업로드합니다. 서비스는 슬라이드를 화면 중앙에 크게 배치하고 녹화 영상을 좌측 하단에 작게 배치한 영상을 만듭니다. 발표자가 다음 슬라이드로 넘어간 시점에 맞춰 화면의 슬라이드도 넘어갑니다.

**2. 쇼츠 자동 생성.** 서비스는 녹화 영상의 내용을 요약하고, 핵심 구간을 골라 YouTube Shorts와 SNS에 올릴 수 있는 세로(9:16) 영상을 만듭니다.

**3. YouTube 자동 업로드.** 서비스는 완성한 영상을 AWSKRUG YouTube 채널에 비공개 상태로 올립니다. 운영진이 내용을 확인한 뒤 공개합니다.

## 디렉토리 구조

| 경로 | 내용 |
|---|---|
| `docs/` | 아키텍처 설계 초안과 참고 프로젝트 검토 결과 |
| `infra/` | AWS CDK(TypeScript) 앱. 서울 리전(`ap-northeast-2`)에 쇼츠 MVP의 리소스를 만드는 스택이 들어 있습니다. |
| `services/slide-sync/` | 기능 1(슬라이드 동기화 영상)의 처리 코드가 들어갈 자리 |
| `services/shorts/` | 기능 2(쇼츠 자동 생성)의 처리 코드. 로컬 명령줄 도구, Fargate에서 실행하는 처리 작업, 컨테이너 이미지 정의 |
| `services/api/` | 웹 화면이 호출하는 API의 Lambda 코드. GitHub 로그인과 작업 관리 |
| `services/common/` | 두 기능이 함께 쓰는 코드(음성 전사, FFmpeg 호출, S3 입출력)가 들어갈 자리 |
| `web/` | 웹 화면. 로그인, 영상 업로드, 구간 후보 선택, 영역 지정, 결과 내려받기 |
| `samples/` | 로컬 시험용 입력 파일을 두는 자리. 영상과 슬라이드 파일은 git에 올리지 않습니다. |

## 문서

- [docs/mvp-shorts.md](docs/mvp-shorts.md): 첫 구현 범위(쇼츠 MVP)와 2026-11-26 인터뷰의 결정 사항
- [docs/plans/2026-11-27-mvp-backend-contract.md](docs/plans/2026-11-27-mvp-backend-contract.md): 쇼츠 MVP의 API 경로, 데이터 형식, 처리 단계, 인프라 구성
- [docs/plans/pending-confirmations.md](docs/plans/pending-confirmations.md): 배포 전에 담당자가 확인하거나 직접 해야 하는 작업
- [docs/architecture.md](docs/architecture.md): 처리 흐름과 AWS 서비스 구성 초안, YouTube 업로드의 전제 조건, 아직 결정하지 않은 사항
- [docs/reference-kshorts-studio.md](docs/reference-kshorts-studio.md): 쇼츠 생성에 `lsszz2100/kshorts-studio`를 쓸 수 있는지 검토한 결과

## 개발

테스트는 세 묶음입니다. Python 테스트는 처리 코드와 API를, Node 테스트는 웹 화면의 계산 함수를, jest 테스트는 합성된 인프라 템플릿을 검사합니다. FFmpeg를 실행하는 Python 테스트는 환경 변수 `SHORTS_FONT_FILE`에 한글 글꼴 파일 경로가 있을 때만 실행됩니다.

```bash
# Python (저장소 루트에서)
uv venv .venv && uv pip install --python .venv/bin/python -e 'services/shorts[test]'
.venv/bin/pytest

# 웹 화면
cd web && npm test

# 인프라
cd infra && npm install && npm test && npx cdk synth
```

`cdk deploy`는 AWS 공용 계정에 리소스와 IAM 역할을 만들므로 담당자의 확인 뒤에 실행합니다.

## 라이선스

[MIT](LICENSE)
