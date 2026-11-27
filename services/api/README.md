# API Lambda (`marketing_api`)

쇼츠 MVP의 HTTP API를 처리하는 Python 3.12 Lambda 코드입니다. 표준 라이브러리와 boto3만 쓰므로 `src/` 폴더를 그대로 코드 자산으로 올립니다. 경로와 응답 형식의 기준은 `docs/plans/2026-11-27-mvp-backend-contract.md`의 3절입니다.

## 핸들러

| 진입점 | 맡은 경로 | 하는 일 |
|---|---|---|
| `marketing_api.authorizer.handler` | 권한 부여자 | `session` 쿠키의 JWT를 검증해 `{"isAuthorized", "context": {"githubId", "login"}}`를 돌려줍니다. 쿠키가 없거나 서명, 만료, 알고리즘이 맞지 않으면 거부합니다. |
| `marketing_api.auth.handler` | `GET /api/auth/login`, `GET /api/auth/callback`, `POST /api/auth/logout` | GitHub 로그인과 팀 멤버 확인, 세션 쿠키 발급과 삭제를 처리합니다. |
| `marketing_api.jobs.handler` | `GET /api/me`, `POST /api/jobs`, `POST /api/jobs/{jobId}/start`, `GET /api/jobs`, `GET /api/jobs/{jobId}`, `POST /api/jobs/{jobId}/renders` | 작업 생성, 분석 시작, 조회, 렌더링 요청을 처리합니다. 요청한 사용자는 권한 부여자가 넘긴 `requestContext.authorizer.lambda`에서 읽습니다. |

## 로그인 흐름

`/api/auth/login`은 무작위 `state` 값을 만들어 `oauth_state` 쿠키(HttpOnly, Secure, SameSite=Lax, 유효 시간 10분)에 넣고 GitHub 승인 화면으로 302 이동시킵니다. `/api/auth/callback`은 쿼리의 `state`와 쿠키 값을 상수 시간 비교로 대조하며, 맞지 않거나 쿠키가 없으면 400을 돌려줍니다. 맞으면 `code`를 사용자 토큰으로 바꾸고, `GET /user`로 사용자를 읽고, 사용자 본인의 토큰으로 팀 멤버십을 확인합니다. `state`가 `active`인 사용자만 멤버로 봅니다. 멤버이면 Users 테이블에 사용자 행을 만들거나 갱신하고(`firstLoginAt`은 첫 로그인 때만 기록), 8시간 유효한 `session` 쿠키를 설정한 뒤 `SITE_URL/`로 302 이동시킵니다. 멤버가 아니면 쿠키와 Users 행 없이 `SITE_URL/?error=not_member`로 이동시킵니다. 사용자 토큰은 이 확인에만 쓰고 저장하지 않습니다. 세션 유효 시간은 `common.SESSION_TTL_SECONDS`입니다.

GitHub 호출이 실패하면(통신 오류, 멤버십 응답이 200과 404 외의 값) 로그인을 허용하지 않고 502를 돌려줍니다. 조직 구성원이 아닌 사용자의 멤버십 조회가 404가 아니라 403으로 오는지는 확인하지 못했습니다. 403이 오면 502가 되며, 그래도 로그인은 허용되지 않습니다.

## 환경 변수

| 이름 | 용도 |
|---|---|
| `MEDIA_BUCKET` | 미디어 버킷 이름 (미리 서명된 URL, 원본 존재 확인) |
| `JOBS_TABLE`, `USERS_TABLE` | DynamoDB 테이블 이름 |
| `ANALYZE_STATE_MACHINE_ARN`, `RENDER_STATE_MACHINE_ARN` | 시작할 Step Functions 상태 기계 |
| `AUTH_SECRET_ARN` | 로그인 비밀 값이 든 Secrets Manager 비밀 |
| `GITHUB_ORG`, `GITHUB_TEAM` | 팀 멤버 확인 대상 (`awskrug`, `sns-working-group`) |
| `SITE_URL` | 웹 주소. 로그인 뒤 이동 주소와 GitHub 콜백 주소의 기준. 비어 있거나 없으면 `GET /api/auth/login`과 `GET /api/auth/callback`이 500과 `{"error": ...}`(SITE_URL 미설정 안내)를 돌려주고, 로그아웃은 그대로 동작합니다. 사용자 도메인이 없는 배포에서는 CDK 컨텍스트 `siteUrl`로 채웁니다(`infra/README.md`). |

필요한 IAM 권한: Jobs 테이블에 `GetItem`, `PutItem`, `UpdateItem`, `Scan`. Users 테이블에 `GetItem`, `UpdateItem`. 미디어 버킷에 `s3:GetObject`, `s3:PutObject`(미리 서명된 URL이 이 권한으로 서명됩니다). 상태 기계 2개에 `states:StartExecution`. 비밀에 `secretsmanager:GetSecretValue`.

## 내려받기 주소

`GET /api/jobs/{jobId}`가 돌려주는 각 결과의 `url`은 `ResponseContentDisposition`을 붙여 서명한 주소라서, S3가 `Content-Disposition: attachment; filename="..."; filename*=UTF-8''...`으로 응답하며 브라우저가 파일로 저장합니다. 파일 이름은 `<작업 제목, 없으면 원본 이름의 확장자 뺀 부분, 없으면 jobId>-<layout>.mp4`입니다. 제목에서 제어 문자와 줄 바꿈은 공백으로 바꾸고, 따옴표, 경로 구분자, `;`, `%`는 지웁니다. ASCII 대체 이름(`filename=`)은 ASCII가 아닌 글자를 `_`로 바꾸고, 원래 이름은 `filename*=`에 UTF-8 퍼센트 인코딩으로 담습니다. `sourceUrl`은 `<video>` 재생용이라 이 헤더를 붙이지 않습니다. 결과 `url`을 `<video>`의 `src`로 써도 재생에는 영향이 없습니다.

## Secrets Manager 비밀

JSON 한 개이며 키는 세 개입니다.

```json
{"githubClientId": "...", "githubClientSecret": "...", "sessionSigningKey": "..."}
```

`sessionSigningKey`는 JWT 서명용 무작위 문자열입니다(32바이트 이상 권장). 값은 배포 뒤 담당자가 직접 넣습니다.

## 사람이 만들어야 하는 GitHub App

`awskrug` 조직 설정의 Developer settings에서 GitHub App을 만듭니다. 아래 항목은 2026-11-27에 공식 문서로 확인한 내용과 구현에서 정한 값입니다.

- Callback URL: `{SITE_URL}/api/auth/callback` (예: `https://marketing.awskr.org/api/auth/callback`). 코드가 승인 요청과 코드 교환에 이 주소를 `redirect_uri`로 보냅니다. 앱 설정의 Callback URL 중 하나와 일치해야 합니다.
- Organization permissions: Members를 Read-only로 설정합니다. 팀 멤버십 확인 API가 이 권한을 요구합니다.
- Account permissions: 따로 줄 필요가 없습니다. `GET /user`는 로그인한 사용자 본인의 공개 프로필을 읽습니다.
- Webhook은 쓰지 않으므로 Active를 끕니다.
- Client ID는 앱 설정 화면에서, Client secret은 같은 화면의 Generate a new client secret으로 만들어 비밀에 넣습니다.
- 앱을 `awskrug` 조직에 설치합니다. 조직 소유자의 승인이 필요합니다. GitHub App의 사용자 토큰은 앱과 사용자가 모두 접근할 수 있는 범위로 제한되므로, 설치가 없으면 Members 권한이 적용되지 않을 것으로 추정합니다. 이 부분은 문서로 직접 확인하지 못했으므로 첫 로그인 시험에서 확인해야 합니다.

## 공식 문서로 확인한 GitHub 사실 (2026-11-27)

- 사용자 토큰 발급: 승인 주소는 `https://github.com/login/oauth/authorize`(`client_id`, `redirect_uri`, `state`), 코드 교환은 `POST https://github.com/login/oauth/access_token`(`client_id`, `client_secret`, `code`, `redirect_uri`). 응답 형식은 `Accept: application/json`을 보내 JSON으로 받습니다. 응답은 `access_token`(`ghu_` 접두사), 토큰 만료를 켠 앱이면 `expires_in`(8시간), `refresh_token` 등입니다. https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app 와 https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- `GET /user`: `Authorization: Bearer 토큰`으로 호출하며 응답에 `login`, `id`, `avatar_url`, `name`이 있습니다. https://docs.github.com/en/rest/users/users
- `GET /orgs/{org}/teams/{team_slug}/memberships/{username}`: 허용하는 토큰은 GitHub App 사용자 액세스 토큰, GitHub App 설치 액세스 토큰, 세분화된 개인 액세스 토큰입니다. 필요한 권한은 조직 권한 Members(읽기)입니다. 응답은 `{"url", "role", "state"}`이고 조직 소유자의 `role`은 `maintainer`입니다. 사용자가 팀 멤버가 아니면 404입니다. 이 엔드포인트는 팀의 조직 구성원으로 인증된 호출자에게만 열려 있고, 호출자에게 보이는 팀이어야 합니다. https://docs.github.com/en/rest/teams/members
- 문서에서 `state`의 값 목록(`active`, `pending`)은 이번에 직접 확인하지 못했습니다. 코드는 `active`만 멤버로 보므로 다른 값은 모두 거부됩니다.

설치 토큰은 앱의 개인 키로 RS256 JWT를 서명해야 하는데 표준 라이브러리에는 RSA 서명이 없어서, 이 구현은 사용자 본인의 토큰을 씁니다.

## 테스트

저장소 루트에서 `.superlite/venv/bin/pytest -q services/api/tests`를 실행합니다. 테스트는 AWS와 GitHub를 가짜로 바꿔 끼우므로 네트워크가 필요 없습니다.
