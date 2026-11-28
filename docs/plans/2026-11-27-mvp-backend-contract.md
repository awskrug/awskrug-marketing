# 쇼츠 MVP 서비스 계약: API, 데이터, 처리 흐름

이 문서는 쇼츠 MVP의 인프라(`infra/`), API Lambda(`services/api/`), 처리 작업(`services/shorts/`의 worker), 웹 화면(`web/`)이 함께 지키는 계약입니다. 네 부분을 동시에 구현하기 위해 2026-11-27에 작성했습니다. 배포 리전은 `ap-northeast-2`(서울)입니다. 이 문서의 구성은 아직 배포해 검증하지 않았습니다.

## 1. 사용자 흐름

사용자는 `sns-working-group` 팀 멤버입니다. 사용자가 GitHub로 로그인하고, 영상 파일을 올리고, 분석을 시작합니다. 서비스가 음성을 뽑아 전사하고 구간 후보를 고릅니다. 사용자가 후보 하나를 고르고, 영상 위에서 발표 화면 영역과 발표자 영역을 사각형으로 지정한 뒤 렌더링을 요청합니다. 서비스가 세 가지 화면 구성으로 렌더링하고, 사용자가 결과를 내려받습니다.

## 2. 저장소

### S3 미디어 버킷 (비공개)

| 키 | 내용 |
|---|---|
| `jobs/{jobId}/source/{fileName}` | 사용자가 올린 원본 영상 |
| `jobs/{jobId}/audio.m4a` | 원본에서 뽑은 음성 |
| `jobs/{jobId}/transcript.json` | Amazon Transcribe 결과 |
| `jobs/{jobId}/renders/{renderId}/{layout}.mp4` | 렌더링 결과. `layout`은 `split`, `blur`, `crop` |
| `assets/logo.png` | 워터마크와 끝 화면에 쓰는 로고. 없으면 로고 없이 렌더링 |

`jobs/` 아래 객체는 30일 뒤 삭제합니다. 브라우저가 미리 서명된 URL로 `PUT` 하므로 버킷 CORS는 웹 주소의 `PUT`과 `GET`을 허용합니다.

### DynamoDB `Jobs` 테이블

파티션 키는 `jobId`(문자열, 32자 16진수)입니다. 정렬 키는 없습니다.

| 속성 | 형식 | 설명 |
|---|---|---|
| `ownerId`, `ownerLogin` | 문자열 | 작업을 만든 사용자의 GitHub 사용자 ID와 로그인 이름 |
| `fileName`, `title` | 문자열 | 원본 파일 이름, 사용자가 붙인 작업 이름 |
| `sourceKey` | 문자열 | 원본의 S3 키 |
| `status` | 문자열 | `UPLOADING`, `QUEUED`, `EXTRACTING`, `TRANSCRIBING`, `SELECTING`, `READY`, `FAILED` |
| `error` | 문자열 | 실패 사유 (`FAILED`일 때) |
| `createdAt`, `updatedAt` | 문자열 | ISO 8601 UTC |
| `width`, `height`, `duration` | 숫자 | 원본 영상의 폭, 높이(픽셀), 길이(초). 음성 추출 단계에서 기록 |
| `candidates` | 목록 | `{start, end, title, reason}` |
| `usage` | 맵 | `{model_id, input_tokens, output_tokens}` |
| `renders` | 맵 | 키는 `renderId`. 값은 `{index, slide, speaker, status, outputs, error, createdAt}` |

`renders`의 `status`는 `RENDERING`, `DONE`, `FAILED`입니다. `outputs`는 `[{layout, key}]`입니다. `slide`와 `speaker`는 `[x, y, w, h]`(원본 픽셀, 정수) 또는 `null`입니다. 권한 등급이 하나이므로 모든 사용자가 모든 작업을 봅니다.

### DynamoDB `Users` 테이블

파티션 키는 `githubId`(문자열)입니다. 속성은 `login`, `name`, `avatarUrl`, `firstLoginAt`, `lastLoginAt`입니다.

## 3. API

모든 경로는 CloudFront 뒤의 같은 도메인에서 `/api`로 시작합니다. 요청과 응답 본문은 JSON입니다. 로그인한 사용자는 `session` 쿠키(HttpOnly, Secure, SameSite=Lax)를 갖습니다. 쿠키 값은 HS256으로 서명한 JWT이고 내용은 `{sub: githubId, login, exp}`입니다. `/api/auth/login`과 `/api/auth/callback`을 뺀 모든 경로는 Lambda 권한 부여자가 이 쿠키를 검증한 뒤에만 실행됩니다. 오류 응답은 `{"error": "설명"}`이며 상태 코드는 400(잘못된 요청), 401(로그인 필요), 403(팀 멤버 아님), 404(없는 작업), 409(현재 상태에서 할 수 없는 요청)를 씁니다.

| 메서드와 경로 | 요청 | 응답 |
|---|---|---|
| `GET /api/auth/login` | 없음 | 302. GitHub 승인 화면으로 이동. `oauth_state` 쿠키 설정 |
| `GET /api/auth/callback?code&state` | 없음 | 302 `/`. 팀 멤버이면 `session` 쿠키 설정, 아니면 302 `/?error=not_member` |
| `POST /api/auth/logout` | 없음 | 204. `session` 쿠키 삭제 |
| `GET /api/me` | 없음 | `{githubId, login, name, avatarUrl}` |
| `POST /api/jobs` | `{fileName, size, contentType, title?}` | 201 `{jobId, uploadUrl, uploadHeaders}` |
| `POST /api/jobs/{jobId}/start` | 없음 | 202 `{jobId, status: "QUEUED"}` |
| `GET /api/jobs` | 없음 | `{jobs: [{jobId, title, fileName, status, ownerLogin, createdAt}]}` 최신순 |
| `GET /api/jobs/{jobId}` | 없음 | 작업 전체 + `sourceUrl` + 각 `outputs` 항목의 `url` |
| `POST /api/jobs/{jobId}/renders` | `{index, slide, speaker}` | 202 `{renderId}` |
| `DELETE /api/jobs/{jobId}` | 없음 | 204 |

`DELETE /api/jobs/{jobId}`는 작업 항목과 미디어 버킷의 `jobs/{jobId}/` 아래 객체를 지웁니다. 작업을 만든 사람만 지울 수 있고, 다른 사람이 요청하면 403을 돌려줍니다. 상태가 `UPLOADING`, `READY`, `FAILED`인 작업만 지울 수 있고, 처리 중인 작업이나 렌더링 중인 항목이 있는 작업은 409를 돌려줍니다. 처리 중인 작업을 지우면 처리 단계가 상태를 갱신하면서 일부 필드만 있는 항목을 다시 만들기 때문입니다. 객체 삭제가 실패해도 작업 항목은 지워진 상태로 두며, 남은 객체는 30일 수명 주기 규칙이 지웁니다.

`POST /api/jobs`는 `size`가 5GB(5,368,709,120바이트)를 넘으면 400을 돌려줍니다. 미리 서명된 단일 `PUT`의 상한이 5GB이기 때문이며, 그보다 큰 파일의 멀티파트 업로드는 MVP에서 지원하지 않습니다. `fileName`에서 경로 구분자와 제어 문자는 `_`로 바꿉니다. 브라우저는 `uploadUrl`로 `uploadHeaders`를 붙여 `PUT` 합니다. `uploadUrl`의 유효 시간은 1시간입니다.

`POST .../start`는 `status`가 `UPLOADING`이고 원본 객체가 S3에 있을 때만 받습니다. 아니면 409입니다. `POST .../renders`는 `status`가 `READY`이고 `index`가 후보 범위 안일 때만 받습니다. `slide`와 `speaker`는 둘 다 주거나 둘 다 `null`이어야 하고, 영상 범위(`width`, `height`) 안이어야 합니다. `sourceUrl`과 결과 `url`은 유효 시간 1시간의 미리 서명된 `GET` 주소입니다.

## 4. 처리 흐름

### 분석 상태 기계 (입력 `{jobId}`)

1. **음성 추출.** Fargate 작업이 `python -m shorts.worker audio --job-id {jobId}`를 실행합니다. 원본을 내려받아 폭, 높이, 길이를 읽고, 음성을 `audio.m4a`로 뽑아 올리고, `status`를 `TRANSCRIBING`으로 바꿉니다. 시작할 때 `status`를 `EXTRACTING`으로 바꿉니다. 음성만 전사하는 이유는 Transcribe의 입력 파일 상한(2GB)을 영상이 넘을 수 있기 때문입니다.
2. **전사.** Step Functions가 Transcribe `StartTranscriptionJob`(작업 이름 `shorts-{jobId}`, `ko-KR`, 결과 키 `jobs/{jobId}/transcript.json`)을 호출하고 20초 간격으로 상태를 조회합니다.
3. **후보 선정.** Lambda `shorts.handlers.select_candidates`가 `{jobId}`를 받아 전사 결과를 읽고 Bedrock 모델을 호출해 `candidates`와 `usage`를 기록하고 `status`를 `READY`로 바꿉니다. 시작할 때 `SELECTING`으로 바꿉니다.

어느 단계든 실패하면 상태 기계가 `status`를 `FAILED`로, `error`를 실패 사유로 기록합니다.

### 렌더링 상태 기계 (입력 `{jobId, renderId}`)

Fargate 작업이 `python -m shorts.worker render --job-id {jobId} --render-id {renderId}`를 실행합니다. 원본과 전사 결과(와 로고가 있으면 로고)를 내려받고, `renders[renderId]`의 `index`, `slide`, `speaker`로 `pipeline.render_candidate`를 호출하고, 결과를 올린 뒤 `outputs`와 `status: DONE`을 기록합니다. 실패하면 상태 기계가 `renders[renderId].status`를 `FAILED`로 기록합니다.

### worker와 Lambda의 환경 변수

| 이름 | 값 |
|---|---|
| `MEDIA_BUCKET` | 미디어 버킷 이름 |
| `JOBS_TABLE`, `USERS_TABLE` | 테이블 이름 |
| `SHORTS_BEDROCK_MODEL_ID` | 기본 `global.anthropic.claude-sonnet-4-6` |
| `SHORTS_FONT_FILE` | 컨테이너 안의 한글 글꼴 파일 경로 |
| `ANALYZE_STATE_MACHINE_ARN`, `RENDER_STATE_MACHINE_ARN` | API Lambda가 시작할 상태 기계 |
| `AUTH_SECRET_ARN` | 로그인 비밀 값이 든 Secrets Manager 비밀 |
| `GITHUB_ORG`, `GITHUB_TEAM` | `awskrug`, `sns-working-group` |
| `SITE_URL` | 웹 주소 (예: `https://marketing.awskr.org`) |

로그인 비밀 값은 JSON 한 개입니다: `{"githubClientId", "githubClientSecret", "sessionSigningKey"}`. 팀 멤버십 확인에 다른 값이 필요하면 로그인 구현이 키를 추가하고 이 문서를 고칩니다. 비밀 값은 저장소에 넣지 않고, 배포 뒤 담당자가 Secrets Manager에 직접 넣습니다.

## 5. 인프라 구성 (CDK)

- **미디어 버킷, 웹 버킷**: 둘 다 비공개. 웹 버킷은 CloudFront 원본 접근 제어(OAC)로만 읽습니다.
- **DynamoDB**: `Jobs`, `Users`. 요청당 과금.
- **컨테이너**: `services/shorts/Dockerfile`(Python 3.12, FFmpeg, 한글 글꼴). Fargate 작업 정의는 4 vCPU, 8GB 메모리, 임시 저장소 50GB.
- **네트워크**: 공개 서브넷만 있는 VPC(가용 영역 2개, NAT 게이트웨이 없음). Fargate 작업은 공인 IP를 받아 S3와 AWS API에 접근합니다. NAT 게이트웨이의 고정 비용을 피하기 위한 선택입니다.
- **Step Functions**: 분석, 렌더링 상태 기계 2개.
- **API**: API Gateway HTTP API + Lambda(Python 3.12). 코드는 `services/api/`에 있고 표준 라이브러리와 boto3만 씁니다.
- **CloudFront**: 기본 동작은 웹 버킷, `/api/*`는 HTTP API. CDK 컨텍스트 `domainName`과 `hostedZoneId`, `certificateArn`이 모두 있으면 그 도메인과 Route 53 별칭 레코드를 붙이고, 없으면 CloudFront 기본 도메인을 씁니다. 인증서는 `us-east-1`에 있어야 하므로 이 스택이 만들지 않고 ARN을 받습니다.
- **비밀**: Secrets Manager 비밀 1개(값은 배포 뒤 담당자가 입력).

## 6. 확인하지 않은 것

- Fargate 4 vCPU에서의 렌더링 소요 시간과 비용. 로컬 컨테이너(CPU 4개, ARM64)에서는 45.6초 구간의 세 구성에 283초가 걸렸습니다(2026-11-27 측정).
- 서울 리전의 Transcribe 한국어 전사 품질과 소요 시간.
- 5GB 단일 `PUT` 업로드가 한국의 일반 회선에서 걸리는 시간.
- Step Functions의 ECS 작업 실행 통합, 렌더링 실패 기록의 표현식, Transcribe가 상태 기계 역할의 권한으로 S3를 읽고 쓰는 동작. 합성된 템플릿으로만 확인했습니다.
- Bedrock 전역 추론 프로필에 건 IAM 조건(`bedrock:InferenceProfileArn`)이 실제 호출을 허용하는지.
- CloudFront에 건 콘텐츠 보안 정책이 실제 화면에서 영상 재생과 업로드를 막지 않는지. 미리 서명된 주소의 호스트가 정책에 넣은 버킷 주소와 같아야 합니다.
- GitHub App을 `awskrug` 조직에 설치해야 사용자 토큰으로 팀 멤버십을 조회할 수 있는지. 공식 문서에서 확인하지 못했고, 설치가 필요하다고 추정합니다.

## 8. 알려진 한계

- 미리 서명된 `PUT`은 업로드 크기를 서버에서 강제하지 않습니다. 사용자가 선언한 `size`와 다른 크기의 파일을 올릴 수 있습니다. 사용자가 팀 멤버 5명으로 한정되고 `jobs/` 아래 객체가 30일 뒤 삭제되므로 MVP에서는 받아들입니다.
- 업로드 주소의 유효 시간은 1시간입니다. 업로드가 1시간을 넘기거나 실패 뒤 1시간이 지나 다시 시도하면 실패하며, 이때는 새 작업을 만들어야 합니다. 웹 화면은 이 경우를 따로 안내하지 않습니다.
- API Gateway의 기본 주소(`execute-api`)도 인터넷에서 직접 접근할 수 있습니다. 이 주소는 Lambda 권한 부여자로만 보호됩니다.
- 로그인 세션의 유효 시간은 8시간입니다. 팀에서 빠진 사람은 세션이 만료될 때까지 최대 8시간 동안 계속 쓸 수 있습니다.

## 7. 코드 위치와 진입점

| 부분 | 위치 | 진입점 |
|---|---|---|
| 처리 작업 | `services/shorts/src/shorts/worker.py` | `python -m shorts.worker audio\|render` |
| 후보 선정 Lambda | `services/shorts/src/shorts/handlers.py` | `shorts.handlers.select_candidates` (코드 자산: `services/shorts/src`) |
| 작업 저장소 접근 | `services/shorts/src/shorts/store.py` | worker와 후보 선정 Lambda가 사용 |
| API Lambda | `services/api/src/marketing_api/` | `marketing_api.jobs.handler`(`/api/jobs*`, `/api/me`), `marketing_api.auth.handler`(`/api/auth/*`), `marketing_api.authorizer.handler` (코드 자산: `services/api/src`) |
| 웹 화면 | `web/` | `web/index.html`. 빌드 단계 없이 `web/` 폴더를 그대로 웹 버킷에 올립니다. |

API Gateway는 HTTP API이고 Lambda 통합의 페이로드 형식은 2.0입니다. 권한 부여자는 단순 응답 형식(`{"isAuthorized": bool, "context": {"githubId", "login"}}`)을 쓰고, 식별 원본은 `Cookie` 헤더입니다. `marketing_api.jobs.handler`는 `event["requestContext"]["authorizer"]["lambda"]`에서 `githubId`와 `login`을 읽습니다.
