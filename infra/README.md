# infra

쇼츠 MVP의 AWS 리소스를 정의하는 AWS CDK(TypeScript) 앱입니다. 서비스 계약은 [docs/plans/2026-11-27-mvp-backend-contract.md](../docs/plans/2026-11-27-mvp-backend-contract.md)의 5절을 따릅니다. 리전은 `ap-northeast-2`(서울)로 고정되어 있고, 계정은 현재 AWS CLI 설정(`CDK_DEFAULT_ACCOUNT`)에서 받습니다. 이 스택은 아직 배포해 검증하지 않았습니다.

## 스택이 만드는 리소스

스택 이름은 `AwskrugMarketingStack`이며 `lib/awskrug-marketing-stack.ts`에 있습니다.

- **S3**: 미디어 버킷과 웹 버킷. 둘 다 비공개이고 SSL을 강제합니다. 미디어 버킷은 `jobs/` 아래 객체를 30일 뒤 삭제하고, 끝나지 않은 멀티파트 업로드를 7일 뒤 정리하며, 웹 주소의 `PUT`, `GET`, `HEAD`를 허용하는 CORS(`ETag` 노출)를 가집니다. 버전 관리는 켜지 않습니다. 이 버킷의 파일은 30일 뒤 지우는 임시 작업 파일이고 같은 키를 다시 쓰는 일은 재처리뿐이라 이전 버전을 보관할 이유가 없기 때문입니다. 두 버킷 모두 스택을 지워도 남도록(RETAIN) 설정했습니다. 웹 버킷은 CloudFront 원본 접근 제어(OAC)로만 읽습니다.
- **DynamoDB**: `Jobs`(파티션 키 `jobId`)와 `Users`(파티션 키 `githubId`). 요청당 과금이며 시점 복구(PITR)를 켰고 RETAIN입니다.
- **네트워크와 컨테이너**: 공개 서브넷만 있는 VPC(가용 영역 `ap-northeast-2a`, `ap-northeast-2c`, NAT 게이트웨이 없음), ECS 클러스터, Fargate 작업 정의(4 vCPU, 8GB, 임시 저장소 50GiB, ARM64). 이미지는 `services/shorts/Dockerfile`을 `linux/arm64`로 빌드합니다. 로그 그룹은 1개월 보관입니다.
- **Lambda(Python 3.12)**: 후보 선정(`shorts.handlers.select_candidates`), API 3개(`marketing_api.jobs.handler`, `marketing_api.auth.handler`, `marketing_api.authorizer.handler`).
- **Step Functions**: 분석 상태 기계(음성 추출, Transcribe 시작, 20초 간격 조회, 후보 선정)와 렌더링 상태 기계. 모든 단계의 실패는 `Jobs` 항목에 `FAILED`와 사유(앞 500자)를 기록한 뒤 실행을 실패로 끝냅니다. Transcribe 작업 이름은 `shorts-{jobId}`라서 같은 `jobId`로 분석을 다시 실행하면 이름이 겹칩니다. 그래서 시작 전에 같은 이름의 이전 작업을 지웁니다(지울 작업이 없어서 나는 오류는 무시합니다). 전사 조회는 20초 간격으로 최대 360번(2시간)까지만 하고, 넘으면 `TranscriptionTimeout`으로 실패를 기록합니다. 이전 작업이 아직 `IN_PROGRESS`라서 지워지지 않으면 시작이 실패해 `FAILED`로 기록됩니다.
- **API Gateway HTTP API**: 계약 3절의 경로. `GET /api/auth/login`, `GET /api/auth/callback`을 뺀 모든 경로에 Lambda 권한 부여자(단순 응답, 식별 원본 `Cookie` 헤더, 결과 캐시 없음)를 붙입니다.
- **Secrets Manager**: 로그인 비밀 1개. `githubClientId`와 `githubClientSecret`은 `REPLACE_ME`로 만들어지고 `sessionSigningKey`는 무작위로 생성됩니다. 배포 뒤 담당자가 GitHub 값 두 개를 채우되, JSON의 키 세 개를 모두 유지해야 합니다.
- **CloudFront**: 기본 동작은 웹 버킷(HTTP는 HTTPS로 넘김, 보안 응답 헤더 정책 적용: HSTS 1년, `X-Content-Type-Options`, `X-Frame-Options: DENY`, `frame-ancestors 'none'`을 포함한 CSP. 스크립트와 스타일은 같은 출처 파일만 허용하고, `<video>`와 업로드는 미디어 버킷 주소로만 허용하며, 아바타는 `avatars.githubusercontent.com`만 허용합니다). 화면은 해시 경로를 쓰므로 오류 페이지 재작성은 두지 않습니다. 사용자 도메인에서는 최소 TLS 1.2를 요구합니다(CloudFront 기본 도메인은 인증서 정책상 지정할 수 없음). `/api/*`는 HTTP API, `/api/*`는 HTTP API(캐시 없음, 모든 메서드, 쿠키와 쿼리 문자열 전달, Host 헤더는 전달하지 않음). `../web` 폴더를 웹 버킷에 올립니다. 이때 `dev`, `tests`, `package.json`, `README.md`는 제외합니다.

## 권한과 보안 메모

각 역할은 코드가 실제로 호출하는 동작만 가집니다. 테스트(`test/stack.test.ts`의 "최소 권한")가 이 범위를 지킵니다.

- worker 작업 역할: `jobs/*`와 `assets/logo.png` 읽기, `jobs/*` 쓰기, 미디어 버킷 `ListBucket`(없는 로고를 403이 아닌 404로 구분하기 위해 필요), `Jobs` 항목의 `GetItem`과 `UpdateItem`. 삭제 권한은 없습니다.
- 상태 기계 역할: `iam:PassRole`은 작업 역할과 실행 역할 두 개로 한정하고, `events:PutRule` 등은 Step Functions가 관리하는 규칙 하나로 한정합니다. Transcribe 동작은 `transcription-job/shorts-*`로 한정하고, 음성 읽기와 전사 결과 쓰기는 `jobs/*`로 한정합니다.
- API 함수: `jobs`는 `jobs/*`의 `GetObject`와 `PutObject`(미리 서명된 URL은 서명한 역할의 권한으로 동작), `Jobs` 항목의 `GetItem`, `PutItem`, `UpdateItem`, `Scan`, `Users`의 `GetItem`, 상태 기계 두 개의 `StartExecution`만 가집니다. `auth`는 `Users`의 `UpdateItem`과 비밀 읽기, 권한 부여자는 비밀 읽기만 가집니다. 비밀은 이 두 함수만 읽습니다.
- 남은 `Resource: "*"`: `ecr:GetAuthorizationToken`(레지스트리 단위 호출이라 자원을 지정할 수 없음), `ecs:DescribeTasks`와 `ecs:StopTask`(Step Functions의 ECS 통합이 CDK에서 이 형태로 만드는 권한), `cloudfront:CreateInvalidation`과 `GetInvalidation`(웹 파일 배포용 CDK 사용자 지정 리소스가 만드는 권한). 이 세 가지는 이 스택 코드가 직접 정한 것이 아니고, 실제 호출 가능 여부는 배포해 봐야 확인됩니다.
- HTTP API 엔드포인트(`execute-api` 주소)는 CloudFront를 거치지 않아도 인터넷에서 직접 접근할 수 있습니다. 보호 수단은 Lambda 권한 부여자뿐이고(로그인 두 경로 제외), 기본 스테이지에 초당 10건, 순간 20건의 제한을 둡니다. CORS는 같은 출처(CloudFront)로만 쓰므로 일부러 설정하지 않았습니다.
- 로그 그룹(worker와 Lambda 4개)은 1개월 보관입니다.

## CDK 컨텍스트

`domainName`, `hostedZoneId`, `certificateArn`이 모두 있을 때만 사용자 도메인을 붙입니다. 이 중 일부만 주면 합성이 오류로 멈춥니다(조용히 무시하지 않습니다). 이때 인증서를 ARN으로 가져와 CloudFront에 연결하고, 지정한 호스팅 영역에 그 이름의 Route 53 별칭 A, AAAA 레코드 2개만 만듭니다. 호스팅 영역이나 다른 레코드는 만들지 않습니다. 인증서는 `us-east-1`에 미리 있어야 합니다. 호스팅 영역 이름은 기본으로 `domainName`에서 첫 레이블을 뺀 값을 쓰며, 다르면 `hostedZoneName`으로 지정합니다. 값은 저장소에 적지 않고 명령줄에서 넘깁니다.

```
npx cdk synth -c domainName=<도메인> -c hostedZoneId=<영역 ID> -c certificateArn=<us-east-1 인증서 ARN>
```

세 값이 없으면 CloudFront 기본 도메인을 씁니다. 이 경우 배포 전에는 그 도메인을 알 수 없습니다. API 함수가 CloudFront를 가리키고 CloudFront가 API를 가리키는 순환 참조가 생기기 때문입니다. 그래서 선택 컨텍스트 `siteUrl`(예: `https://dxxxx.cloudfront.net`, 경로 없는 `https://` 주소)을 둡니다. 웹 주소(`SITE_URL`)와 미디어 버킷 CORS 허용 출처는 다음 순서로 정해집니다.

| 조건 | `SITE_URL` | 미디어 버킷 CORS 허용 출처 |
|---|---|---|
| 사용자 도메인 세 값이 있음 (`siteUrl`이 있어도 무시) | `https://<도메인>` | `https://<도메인>` |
| 사용자 도메인 없음, `siteUrl` 있음 | `siteUrl` 값 | `siteUrl` 값 |
| 둘 다 없음 | 빈 문자열 | 모든 출처(`*`) |

`SITE_URL`이 비어 있으면 GitHub 로그인에 필요한 절대 `redirect_uri`를 만들 수 없으므로 `GET /api/auth/login`과 `GET /api/auth/callback`이 500과 안내 메시지를 돌려줍니다. 로그아웃은 동작합니다. 사용자 도메인이 없는 첫 배포는 두 단계로 합니다.

1. `siteUrl` 없이 배포합니다. 출력의 `DistributionDomain` 값(예: `dxxxx.cloudfront.net`)을 읽습니다.
2. 그 값으로 `-c siteUrl=https://<DistributionDomain 값>`을 붙여 다시 배포합니다. 두 번째 배포는 API 함수의 `SITE_URL`과 CORS 허용 출처만 바꿉니다. 이어서 GitHub App의 Callback URL을 `https://<DistributionDomain 값>/api/auth/callback`으로 맞춥니다.

```
npx cdk deploy -c siteUrl=https://<DistributionDomain 값>
```

이 명령은 실제 리소스를 바꾸므로 아래 "배포"의 확인 절차를 먼저 거칩니다.

## 명령

- `npm run build`: TypeScript 타입을 검사합니다.
- `npm test`: jest로 합성한 템플릿을 검사합니다.
- `npx cdk synth -q`: CloudFormation 템플릿을 생성합니다. AWS를 조회하지 않으며 Docker도 필요 없습니다. `../services/shorts/Dockerfile`, `../services/api/src`, `../web`이 있어야 합니다.

## 배포

`cdk deploy`는 AWSKRUG 공용 계정의 실제 리소스를 만들고 바꿉니다. 사람의 확인 없이 실행하지 않습니다. 실행 전에 `cdk diff`로 변경 내용을 확인하고, AWS 로그인 세션은 `awskrug` 프로필을 사용합니다.
