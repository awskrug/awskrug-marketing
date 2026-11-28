import { readFileSync } from 'fs';
import * as path from 'path';
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AwskrugMarketingStack } from '../lib/awskrug-marketing-stack';

const DOMAIN_CONTEXT = {
  domainName: 'marketing.example.org',
  hostedZoneId: 'Z0000000FAKE',
  certificateArn: 'arn:aws:acm:us-east-1:111111111111:certificate/00000000-0000-0000-0000-000000000000',
};

function synth(context: Record<string, string> = {}): Template {
  const app = new App({ context });
  const stack = new AwskrugMarketingStack(app, 'TestStack', {
    env: { account: '111111111111', region: 'ap-northeast-2' },
  });
  return Template.fromStack(stack);
}

describe('기본 구성 (사용자 도메인 없음)', () => {
  const t = synth();

  test('S3 버킷 2개 모두 퍼블릭 액세스를 막는다', () => {
    const buckets = t.findResources('AWS::S3::Bucket');
    const real = Object.values(buckets).filter((b: any) => b.Properties?.PublicAccessBlockConfiguration);
    expect(real).toHaveLength(2);
    for (const b of real as any[]) {
      expect(b.Properties.PublicAccessBlockConfiguration).toEqual({
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      });
    }
  });

  test('미디어 버킷: 수명 주기와 CORS, 유지 정책', () => {
    t.hasResource('AWS::S3::Bucket', {
      DeletionPolicy: 'Retain',
      Properties: {
        LifecycleConfiguration: {
          Rules: Match.arrayWith([
            Match.objectLike({ Prefix: 'jobs/', ExpirationInDays: 30, Status: 'Enabled' }),
            Match.objectLike({ AbortIncompleteMultipartUpload: { DaysAfterInitiation: 7 } }),
          ]),
        },
        CorsConfiguration: {
          CorsRules: [Match.objectLike({ AllowedMethods: ['PUT', 'GET', 'HEAD'], AllowedOrigins: ['*'], ExposedHeaders: ['ETag'] })],
        },
      },
    });
  });

  test('DynamoDB 테이블 2개, 요청당 과금, PITR', () => {
    t.resourceCountIs('AWS::DynamoDB::Table', 2);
    t.allResourcesProperties('AWS::DynamoDB::Table', {
      BillingMode: 'PAY_PER_REQUEST',
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
    });
    t.hasResourceProperties('AWS::DynamoDB::Table', { KeySchema: [{ AttributeName: 'jobId', KeyType: 'HASH' }] });
    t.hasResourceProperties('AWS::DynamoDB::Table', { KeySchema: [{ AttributeName: 'githubId', KeyType: 'HASH' }] });
  });

  test('NAT 게이트웨이가 없다', () => {
    t.resourceCountIs('AWS::EC2::NatGateway', 0);
  });

  test('Fargate 작업 정의 크기', () => {
    t.hasResourceProperties('AWS::ECS::TaskDefinition', {
      Cpu: '4096',
      Memory: '8192',
      EphemeralStorage: { SizeInGiB: 50 },
      RuntimePlatform: Match.objectLike({ CpuArchitecture: 'ARM64' }),
    });
  });

  test('상태 기계 2개', () => {
    t.resourceCountIs('AWS::StepFunctions::StateMachine', 2);
  });

  test('Lambda는 Python 3.12', () => {
    for (const handler of [
      'shorts.handlers.select_candidates',
      'marketing_api.jobs.handler',
      'marketing_api.auth.handler',
      'marketing_api.authorizer.handler',
    ]) {
      t.hasResourceProperties('AWS::Lambda::Function', { Handler: handler, Runtime: 'python3.12' });
    }
  });

  test('API 경로: 로그인 두 경로만 권한 부여자가 없다', () => {
    const routes = Object.values(t.findResources('AWS::ApiGatewayV2::Route')).map((r: any) => r.Properties);
    const keys = routes.map((r) => r.RouteKey).sort();
    expect(keys).toEqual(
      [
        'DELETE /api/jobs/{jobId}',
        'GET /api/auth/callback',
        'GET /api/auth/login',
        'GET /api/jobs',
        'GET /api/jobs/{jobId}',
        'GET /api/me',
        'POST /api/auth/logout',
        'POST /api/jobs',
        'POST /api/jobs/{jobId}/renders',
        'POST /api/jobs/{jobId}/start',
      ].sort(),
    );
    for (const r of routes) {
      const open = r.RouteKey === 'GET /api/auth/login' || r.RouteKey === 'GET /api/auth/callback';
      if (open) {
        expect(r.AuthorizationType).toBe('NONE');
        expect(r.AuthorizerId).toBeUndefined();
      } else {
        expect(r.AuthorizationType).toBe('CUSTOM');
        expect(r.AuthorizerId).toBeDefined();
      }
    }
    t.hasResourceProperties('AWS::ApiGatewayV2::Authorizer', {
      AuthorizerType: 'REQUEST',
      AuthorizerPayloadFormatVersion: '2.0',
      EnableSimpleResponses: true,
      IdentitySource: ['$request.header.Cookie'],
      AuthorizerResultTtlInSeconds: 0,
    });
  });

  test('CloudFront: /api/* 는 캐시 없이 모든 메서드 허용', () => {
    t.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        DefaultRootObject: 'index.html',
        CacheBehaviors: [Match.objectLike({ PathPattern: '/api/*', AllowedMethods: Match.arrayWith(['POST', 'DELETE']) })],
      }),
    });
  });

  test('Route 53 리소스가 없다', () => {
    expect(Object.keys(t.toJSON().Resources).filter((k) => t.toJSON().Resources[k].Type.startsWith('AWS::Route53'))).toEqual([]);
  });

  test('비밀은 유지 정책', () => {
    t.hasResource('AWS::SecretsManager::Secret', { DeletionPolicy: 'Retain' });
  });

  test('Fargate 컨테이너는 글꼴 환경 변수를 덮어쓰지 않는다 (이미지의 ENV가 적용된다)', () => {
    const defs = Object.values(t.findResources('AWS::ECS::TaskDefinition')) as any[];
    expect(defs).toHaveLength(1);
    const env = defs[0].Properties.ContainerDefinitions[0].Environment as { Name: string }[];
    const names = env.map((e) => e.Name);
    expect(names).not.toContain('SHORTS_FONT_FILE');
    expect(names).not.toContain('SHORTS_FONT_NAME');
    expect(names).toEqual(expect.arrayContaining(['MEDIA_BUCKET', 'JOBS_TABLE']));
  });

  test('사용자 도메인도 siteUrl도 없으면 SITE_URL은 빈 문자열, CORS는 *', () => {
    for (const handler of ['marketing_api.jobs.handler', 'marketing_api.auth.handler']) {
      t.hasResourceProperties('AWS::Lambda::Function', {
        Handler: handler,
        Environment: { Variables: Match.objectLike({ SITE_URL: '' }) },
      });
    }
  });
});

describe('siteUrl 컨텍스트', () => {
  const SITE = 'https://d111111abcdef8.cloudfront.net';

  test('사용자 도메인이 없으면 SITE_URL과 CORS 허용 출처로 쓴다', () => {
    const t = synth({ siteUrl: SITE });
    for (const handler of ['marketing_api.jobs.handler', 'marketing_api.auth.handler', 'marketing_api.authorizer.handler']) {
      t.hasResourceProperties('AWS::Lambda::Function', {
        Handler: handler,
        Environment: { Variables: Match.objectLike({ SITE_URL: SITE }) },
      });
    }
    t.hasResourceProperties('AWS::S3::Bucket', {
      CorsConfiguration: { CorsRules: [Match.objectLike({ AllowedOrigins: [SITE] })] },
    });
    t.resourceCountIs('AWS::Route53::RecordSet', 0);
  });

  test('끝의 슬래시는 지운다', () => {
    synth({ siteUrl: `${SITE}/` }).hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'marketing_api.jobs.handler',
      Environment: { Variables: Match.objectLike({ SITE_URL: SITE }) },
    });
  });

  test('사용자 도메인이 있으면 siteUrl보다 도메인이 우선한다', () => {
    const t = synth({ ...DOMAIN_CONTEXT, siteUrl: SITE });
    t.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'marketing_api.jobs.handler',
      Environment: { Variables: Match.objectLike({ SITE_URL: 'https://marketing.example.org' }) },
    });
    t.hasResourceProperties('AWS::S3::Bucket', {
      CorsConfiguration: { CorsRules: [Match.objectLike({ AllowedOrigins: ['https://marketing.example.org'] })] },
    });
  });

  test('https://호스트 형식이 아니면 합성을 거부한다', () => {
    expect(() => synth({ siteUrl: 'http://d111.cloudfront.net' })).toThrow(/siteUrl/);
    expect(() => synth({ siteUrl: 'https://d111.cloudfront.net/path' })).toThrow(/siteUrl/);
  });
});

describe('서비스 사이의 이음새', () => {
  const t = synth();
  const flatten = (x: any): string =>
    typeof x === 'string' ? x : x?.['Fn::Join'] ? x['Fn::Join'][1].map(flatten).join('') : '';
  const definitions = Object.values(t.findResources('AWS::StepFunctions::StateMachine')).map((m: any) =>
    flatten(m.Properties.DefinitionString),
  );
  const policies = Object.values(t.findResources('AWS::IAM::Policy')).map((p: any) => p.Properties);
  const actionsFor = (rolePrefix: string): string[] =>
    policies
      .filter((p) => p.Roles.some((r: any) => r.Ref.startsWith(rolePrefix)))
      .flatMap((p) => p.PolicyDocument.Statement.flatMap((s: any) => [].concat(s.Action)));

  test('상태 기계가 worker argparse와 같은 명령 배열을 넘긴다', () => {
    const joined = definitions.join('\n');
    expect(joined).toContain("States.Array('python', '-m', 'shorts.worker', 'audio', '--job-id', $.jobId)");
    expect(joined).toContain(
      "States.Array('python', '-m', 'shorts.worker', 'render', '--job-id', $.jobId, '--render-id', $.renderId)",
    );
  });

  test('실패 처리기는 API가 쓰는 속성 이름으로 기록한다', () => {
    const joined = definitions.join('\n');
    expect(joined).toContain('SET #s = :s, #e = :e, updatedAt = :u');
    expect(joined).toContain('SET renders.#r.#s = :s, renders.#r.#e = :e, updatedAt = :u');
    expect(joined).toContain('"#r.$":"$.renderId"');
  });

  test('API 함수 역할은 서명, 시작 확인, 상태 기계 시작, 목록 조회 권한을 가진다', () => {
    const actions = actionsFor('JobsFnServiceRole');
    for (const a of ['s3:GetObject', 's3:PutObject', 'states:StartExecution', 'dynamodb:Scan',
      'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem']) {
      expect(actions).toContain(a);
    }
  });

  test('worker와 후보 선정 Lambda 역할은 코드가 호출하는 권한을 가진다', () => {
    for (const a of ['s3:GetObject', 's3:PutObject', 's3:ListBucket', 'dynamodb:GetItem', 'dynamodb:UpdateItem']) {
      expect(actionsFor('WorkerTaskTaskRole')).toContain(a);
    }
    for (const a of ['s3:GetObject', 'dynamodb:GetItem', 'dynamodb:UpdateItem', 'bedrock:InvokeModel']) {
      expect(actionsFor('SelectCandidatesFnServiceRole')).toContain(a);
    }
  });

  test('코드가 읽는 환경 변수 이름이 모두 설정된다', () => {
    const names = (handler: string): string[] => {
      const fn: any = Object.values(t.findResources('AWS::Lambda::Function')).find(
        (f: any) => f.Properties.Handler === handler,
      );
      return Object.keys(fn.Properties.Environment.Variables);
    };
    expect(names('marketing_api.jobs.handler')).toEqual(
      expect.arrayContaining(['MEDIA_BUCKET', 'JOBS_TABLE', 'USERS_TABLE', 'ANALYZE_STATE_MACHINE_ARN',
        'RENDER_STATE_MACHINE_ARN', 'SITE_URL']),
    );
    expect(names('marketing_api.auth.handler')).toEqual(
      expect.arrayContaining(['AUTH_SECRET_ARN', 'GITHUB_ORG', 'GITHUB_TEAM', 'SITE_URL', 'USERS_TABLE']),
    );
    expect(names('shorts.handlers.select_candidates')).toEqual(
      expect.arrayContaining(['MEDIA_BUCKET', 'JOBS_TABLE', 'SHORTS_BEDROCK_MODEL_ID']),
    );
  });

  test('비밀은 계약의 세 키를 만든다', () => {
    const secret: any = Object.values(t.findResources('AWS::SecretsManager::Secret'))[0];
    const gen = secret.Properties.GenerateSecretString;
    expect(Object.keys(JSON.parse(gen.SecretStringTemplate)).sort()).toEqual(['githubClientId', 'githubClientSecret']);
    expect(gen.GenerateStringKey).toBe('sessionSigningKey');
  });
});

describe('사용자 도메인 컨텍스트', () => {
  test('세 값이 모두 있으면 별칭 A/AAAA 레코드만 만든다', () => {
    const t = synth(DOMAIN_CONTEXT);
    t.resourceCountIs('AWS::Route53::RecordSet', 2);
    t.resourceCountIs('AWS::Route53::HostedZone', 0);
    t.hasResourceProperties('AWS::Route53::RecordSet', {
      Type: 'A', Name: 'marketing.example.org.', HostedZoneId: 'Z0000000FAKE',
    });
    t.hasResourceProperties('AWS::Route53::RecordSet', { Type: 'AAAA', Name: 'marketing.example.org.' });
    t.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Aliases: ['marketing.example.org'],
        ViewerCertificate: Match.objectLike({ AcmCertificateArn: DOMAIN_CONTEXT.certificateArn }),
      }),
    });
    t.hasResourceProperties('AWS::Lambda::Function', {
      Environment: { Variables: Match.objectLike({ SITE_URL: 'https://marketing.example.org' }) },
    });
    t.hasResourceProperties('AWS::S3::Bucket', {
      CorsConfiguration: { CorsRules: [Match.objectLike({ AllowedOrigins: ['https://marketing.example.org'] })] },
    });
  });

  test('일부 값만 있으면 조용히 무시하지 않고 합성을 거부한다', () => {
    expect(() => synth({ domainName: 'marketing.example.org' })).toThrow(/세 값을 모두/);
    expect(() => synth({ hostedZoneId: DOMAIN_CONTEXT.hostedZoneId, certificateArn: DOMAIN_CONTEXT.certificateArn }))
      .toThrow(/domainName=없음/);
  });

  test('사용자 도메인이 있으면 TLS 1.2 이상만 받는다', () => {
    synth(DOMAIN_CONTEXT).hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({ ViewerCertificate: Match.objectLike({ MinimumProtocolVersion: 'TLSv1.2_2021' }) }),
    });
  });
});

describe('최소 권한', () => {
  const t = synth();
  const policies = Object.values(t.findResources('AWS::IAM::Policy')).map((p: any) => p.Properties);
  const statements = (rolePrefix: string): any[] =>
    policies
      .filter((p) => p.Roles.some((r: any) => r.Ref.startsWith(rolePrefix)))
      .flatMap((p) => p.PolicyDocument.Statement);
  const actions = (rolePrefix: string): string[] => statements(rolePrefix).flatMap((s) => [].concat(s.Action));
  const json = (x: unknown) => JSON.stringify(x);

  test('역할 정책에 와일드카드 동작이 없다 (서비스 전체 *)', () => {
    for (const p of policies) {
      for (const s of p.PolicyDocument.Statement) {
        for (const a of ([] as string[]).concat(s.Action)) {
          expect(a).not.toBe('*');
          expect(a).not.toMatch(/^[a-z0-9-]+:\*$/);
        }
      }
    }
  });

  test('Resource "*"는 지정이 불가능한 호출에만 쓰인다', () => {
    const allowed = new Set([
      'ecr:GetAuthorizationToken', // 레지스트리 단위 호출
      'ecs:DescribeTasks', // Step Functions의 ECS 통합이 요구하는 형태(작업 ID를 미리 모름)
      'ecs:StopTask',
      'cloudfront:CreateInvalidation', // BucketDeployment 사용자 지정 리소스(CDK 관리)
      'cloudfront:GetInvalidation',
    ]);
    for (const p of policies) {
      for (const s of p.PolicyDocument.Statement) {
        if (([] as string[]).concat(s.Resource ?? []).includes('*')) {
          for (const a of ([] as string[]).concat(s.Action)) expect(allowed).toContain(a);
        }
      }
    }
  });

  test('상태 기계의 iam:PassRole은 작업 역할과 실행 역할로 한정된다', () => {
    for (const prefix of ['AnalyzeStateMachineRole', 'RenderStateMachineRole']) {
      const pass = statements(prefix).filter((s) => ([] as string[]).concat(s.Action).includes('iam:PassRole'));
      expect(pass).toHaveLength(1);
      expect(pass[0].Resource).toHaveLength(2);
      expect(json(pass[0].Resource)).toContain('WorkerTaskTaskRole');
      expect(json(pass[0].Resource)).toContain('WorkerTaskExecutionRole');
    }
  });

  test('ECS 작업 통합용 events 규칙 권한은 Step Functions 관리 규칙 하나로 한정된다', () => {
    const ev = statements('AnalyzeStateMachineRole').filter((s) => json(s.Action).includes('events:'));
    expect(ev).toHaveLength(1);
    expect(json(ev[0].Resource)).toContain('rule/StepFunctionsGetEventsForECSTaskRule');
  });

  test('worker 역할: 삭제 권한 없음, 쓰기는 jobs/ 아래만, Jobs 항목 조회와 갱신만', () => {
    const acts = actions('WorkerTaskTaskRole');
    expect(acts.sort()).toEqual(['dynamodb:GetItem', 'dynamodb:UpdateItem', 's3:GetObject', 's3:ListBucket', 's3:PutObject']);
    const put = statements('WorkerTaskTaskRole').find((s) => s.Action === 's3:PutObject');
    expect(json(put.Resource)).toContain('/jobs/*');
    expect(json(put.Resource)).not.toContain('"/*"');
  });

  test('후보 선정 역할: 전사 읽기, Jobs 갱신, 지정한 모델 호출만', () => {
    const acts = actions('SelectCandidatesFnServiceRole');
    expect(acts.sort()).toEqual(['bedrock:InvokeModel', 'bedrock:InvokeModel', 'dynamodb:GetItem', 'dynamodb:UpdateItem', 's3:GetObject']);
    for (const s of statements('SelectCandidatesFnServiceRole').filter((x) => x.Action === 'bedrock:InvokeModel')) {
      expect(json(s.Resource)).not.toContain('"*"');
    }
  });

  test('API 함수: jobs는 상태 기계 두 개만 시작하고, 삭제는 작업 항목과 jobs/ 객체로 한정한다', () => {
    const st = statements('JobsFnServiceRole').filter((s) => s.Action === 'states:StartExecution');
    expect(st.flatMap((s) => [].concat(s.Resource))).toHaveLength(2);
    expect(actions('JobsFnServiceRole').filter((a) => /Delete|Batch/.test(a)).sort()).toEqual(
      ['dynamodb:DeleteItem', 's3:DeleteObject']);
    for (const s of statements('JobsFnServiceRole').filter((s) => [].concat(s.Action).includes('s3:DeleteObject'))) {
      expect(json(s.Resource)).toContain('/jobs/*');
    }
    const list = statements('JobsFnServiceRole').filter((s) => [].concat(s.Action).includes('s3:ListBucket'));
    expect(list).toHaveLength(1);
    expect(list[0].Condition).toEqual({ StringLike: { 's3:prefix': ['jobs/*'] } });
    expect(actions('JobsFnServiceRole')).not.toContain('secretsmanager:GetSecretValue');
  });

  test('비밀은 auth와 권한 부여자 함수만 읽는다', () => {
    const readers = policies
      .filter((p) => p.PolicyDocument.Statement.some((s: any) => json(s.Action).includes('secretsmanager:GetSecretValue')))
      .flatMap((p) => p.Roles.map((r: any) => r.Ref))
      .sort();
    expect(readers).toHaveLength(2);
    expect(readers[0]).toMatch(/^AuthFnServiceRole/);
    expect(readers[1]).toMatch(/^AuthorizerFnServiceRole/);
  });

  test('분석 상태 기계 역할은 Transcribe 작업 이름 접두사 shorts-로 한정된다', () => {
    const tr = statements('AnalyzeStateMachineRole').filter((s) => json(s.Action).includes('transcribe:'));
    expect(tr.map((s) => s.Action.toLowerCase()).sort()).toEqual([
      'transcribe:deletetranscriptionjob', 'transcribe:gettranscriptionjob', 'transcribe:starttranscriptionjob',
    ]);
    for (const s of tr) expect(json(s.Resource)).toContain('transcription-job/shorts-*');
    // 음성 읽기와 전사 결과 쓰기는 jobs/ 아래만 허용한다.
    const s3s = statements('AnalyzeStateMachineRole').filter((s) => json(s.Action).includes('s3:'));
    for (const s of s3s) expect(json(s.Resource)).toContain('/jobs/*');
  });
});

describe('상태 기계 정의', () => {
  const t = synth();
  const flatten = (x: any): string =>
    typeof x === 'string' ? x : x?.['Fn::Join'] ? x['Fn::Join'][1].map(flatten).join('') : x?.Ref ? 'REF' : 'REF';
  const defs = Object.values(t.findResources('AWS::StepFunctions::StateMachine')).map((m: any) =>
    JSON.parse(flatten(m.Properties.DefinitionString)),
  );
  const [analyze, render] = defs;

  test('Transcribe 시작 매개변수는 계약과 같다', () => {
    const p = analyze.States.StartTranscription.Parameters;
    expect(p['TranscriptionJobName.$']).toBe("States.Format('shorts-{}', $.jobId)");
    expect(p.LanguageCode).toBe('ko-KR');
    expect(p['Media'] ['MediaFileUri.$']).toBe("States.Format('s3://{}/jobs/{}/audio.m4a', 'REF', $.jobId)");
    expect(p['OutputKey.$']).toBe("States.Format('jobs/{}/transcript.json', $.jobId)");
    expect(p.OutputBucketName).toBeDefined();
  });

  test('시작 전에 같은 이름의 이전 Transcribe 작업을 지우고, 지울 것이 없어도 입력을 잃지 않고 이어간다', () => {
    const d = analyze.States.DeleteOldTranscription;
    expect(d.Resource).toContain('transcribe:deleteTranscriptionJob');
    expect(d.Next).toBe('StartTranscription');
    expect(d.Catch).toEqual([{ ErrorEquals: ['States.ALL'], ResultPath: '$.deleteError', Next: 'StartTranscription' }]);
  });

  test('조회 반복은 COMPLETED와 FAILED에서 끝나고 횟수 제한이 있다', () => {
    const c = analyze.States['TranscriptionDone?'];
    expect(c.Choices.map((x: any) => [x.StringEquals, x.Next])).toEqual([
      ['COMPLETED', 'SelectCandidates'],
      ['FAILED', 'TranscriptionFailed'],
    ]);
    expect(c.Default).toBe('BumpPollCount');
    expect(analyze.States['PollLimitReached?'].Choices[0]).toMatchObject({
      Variable: '$.poll.count', NumericGreaterThan: 360, Next: 'TranscriptionTimedOut',
    });
    expect(analyze.States['PollLimitReached?'].Default).toBe('WaitTranscription');
    expect(analyze.States.InitPollCount.Next).toBe('WaitTranscription');
    expect(analyze.TimeoutSeconds).toBeGreaterThan(0);
  });

  test('후보 선정 Lambda는 jobId만 받고 일시적인 Lambda 오류에서만 다시 시도한다', () => {
    const s = analyze.States.SelectCandidates;
    expect(s.Parameters.Payload).toEqual({ 'jobId.$': '$.jobId' });
    for (const r of s.Retry) {
      for (const e of r.ErrorEquals) expect(e).toMatch(/^Lambda\./);
    }
  });

  test('모든 작업 상태에 Catch가 있고 실패는 사유를 500자로 줄여 기록한 뒤 Fail로 끝난다', () => {
    for (const [name, st] of Object.entries<any>({ ...analyze.States, ...render.States })) {
      if (st.Type === 'Task' && !/MarkFailed|DeleteOldTranscription/.test(name)) {
        expect(st.Catch?.[0]?.ErrorEquals).toEqual(['States.ALL']);
        expect(st.Catch[0].Next).toMatch(/TrimError$/);
      }
    }
    for (const trim of [analyze.States.AnalyzeTrimError, render.States.RenderTrimError]) {
      expect(trim.QueryLanguage).toBe('JSONata');
      expect(trim.Output.errorText).toContain('$substring(');
      expect(trim.Output.errorText).toContain(', 0, 500)');
    }
    expect(analyze.States.AnalyzeMarkFailed.Next).toBe('AnalyzeFail');
    expect(analyze.States.AnalyzeFail.Type).toBe('Fail');
    expect(analyze.States.AnalyzeMarkFailed.Parameters.ExpressionAttributeValues[':e']).toEqual({ 'S.$': '$.errorText' });
    expect(render.States.RenderMarkFailed.Next).toBe('RenderFail');
    expect(render.States.RenderFail.Type).toBe('Fail');
  });

  test('Fargate 작업은 공인 IP를 받는다', () => {
    for (const st of [analyze.States.ExtractAudioRunTask, render.States.RenderRunTask]) {
      expect(st.Parameters.NetworkConfiguration.AwsvpcConfiguration.AssignPublicIp).toBe('ENABLED');
      expect(st.Parameters.LaunchType).toBe('FARGATE');
    }
  });

  test('렌더링 실패 처리기는 표현식 속성 이름으로 renders 키를 가리킨다', () => {
    const p = render.States.RenderMarkFailed.Parameters;
    expect(p.ExpressionAttributeNames).toEqual({ '#r.$': '$.renderId', '#s': 'status', '#e': 'error' });
    expect(p.UpdateExpression).toContain('renders.#r.#s = :s');
    expect(render.States.RenderTrimError.Output.renderId).toBe('{% $states.input.renderId %}');
  });
});

describe('네트워크, 저장소, CDN, API 설정', () => {
  const t = synth();

  test('Fargate 보안 그룹은 인바운드 규칙이 없고 작업은 공인 IP를 받는다', () => {
    const sgs = Object.values(t.findResources('AWS::EC2::SecurityGroup')) as any[];
    for (const sg of sgs) expect(sg.Properties.SecurityGroupIngress ?? []).toEqual([]);
    t.resourceCountIs('AWS::EC2::SecurityGroupIngress', 0);
  });

  test('VPC에는 공개 서브넷만 있다', () => {
    const subnets = Object.values(t.findResources('AWS::EC2::Subnet')) as any[];
    expect(subnets.length).toBe(2);
    for (const s of subnets) expect(s.Properties.MapPublicIpOnLaunch).toBe(true);
  });

  test('두 버킷은 SSL 강제와 암호화가 켜져 있다', () => {
    const buckets = Object.values(t.findResources('AWS::S3::Bucket')).filter((b: any) => b.Properties?.PublicAccessBlockConfiguration) as any[];
    for (const b of buckets) {
      expect(JSON.stringify(b.Properties.BucketEncryption)).toContain('AES256');
      expect(b.Properties.VersioningConfiguration).toBeUndefined();
    }
    expect(JSON.stringify(Object.values(t.findResources('AWS::S3::BucketPolicy')))).toContain('aws:SecureTransport');
  });

  test('웹 버킷은 CloudFront 서비스 주체만 읽는다 (OAC)', () => {
    t.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);
    expect(JSON.stringify(Object.values(t.findResources('AWS::S3::BucketPolicy')))).toContain('cloudfront.amazonaws.com');
  });

  test('웹 배포는 개발과 시험 파일을 제외한다', () => {
    const src = readFileSync(path.join(__dirname, '..', 'lib', 'awskrug-marketing-stack.ts'), 'utf-8');
    for (const x of ["'dev'", "'tests'", "'package.json'", "'README.md'"]) expect(src).toContain(x);
  });

  test('CloudFront: 기본 동작은 HTTPS로 넘기고, /api/*는 쿠키와 쿼리를 전달하되 Host는 전달하지 않는다', () => {
    const dist: any = Object.values(t.findResources('AWS::CloudFront::Distribution'))[0];
    const cfg = dist.Properties.DistributionConfig;
    expect(cfg.DefaultCacheBehavior.ViewerProtocolPolicy).toBe('redirect-to-https');
    expect(cfg.DefaultCacheBehavior.ResponseHeadersPolicyId).toBeDefined();
    expect(cfg.CustomErrorResponses).toBeUndefined();
    expect(cfg.CacheBehaviors[0].CachePolicyId).toBe('4135ea2d-6df8-44a3-9df3-4b5a84be39ad'); // CachingDisabled
    expect(cfg.CacheBehaviors[0].OriginRequestPolicyId).toBe('b689b0a8-53d0-40ab-baf2-68738e2966ac'); // AllViewerExceptHostHeader
    expect(cfg.CacheBehaviors[0].AllowedMethods).toEqual(['GET', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'POST', 'DELETE']);
  });

  test('보안 응답 헤더: HSTS, nosniff, DENY, CSP(같은 출처 스크립트와 스타일, 미디어 버킷 주소)', () => {
    const p: any = Object.values(t.findResources('AWS::CloudFront::ResponseHeadersPolicy'))[0];
    const h = p.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig;
    expect(h.StrictTransportSecurity.AccessControlMaxAgeSec).toBeGreaterThanOrEqual(31536000);
    expect(h.ContentTypeOptions.Override).toBe(true);
    expect(h.FrameOptions.FrameOption).toBe('DENY');
    const csp = JSON.stringify(h.ContentSecurityPolicy.ContentSecurityPolicy);
    for (const d of ["default-src 'none'", "script-src 'self'", "style-src 'self'", "frame-ancestors 'none'", 'media-src', 'connect-src', 'MediaBucket']) {
      expect(csp).toContain(d);
    }
    expect(csp).not.toContain('unsafe-inline');
    expect(csp).not.toContain('unsafe-eval');
  });

  test('HTTP API: 기본 스테이지 제한 설정, CORS 설정 없음', () => {
    t.hasResourceProperties('AWS::ApiGatewayV2::Stage', {
      DefaultRouteSettings: { ThrottlingRateLimit: 10, ThrottlingBurstLimit: 20 },
    });
    const api: any = Object.values(t.findResources('AWS::ApiGatewayV2::Api'))[0];
    expect(api.Properties.CorsConfiguration).toBeUndefined();
  });

  test('모든 Lambda와 Fargate 로그 그룹은 보관 기간이 있다', () => {
    const groups = Object.values(t.findResources('AWS::Logs::LogGroup')) as any[];
    expect(groups.length).toBeGreaterThanOrEqual(5);
    for (const g of groups) expect(g.Properties.RetentionInDays).toBeGreaterThan(0);
    for (const f of Object.values(t.findResources('AWS::Lambda::Function')) as any[]) {
      if (/^(shorts|marketing_api)\./.test(f.Properties.Handler)) expect(f.Properties.LoggingConfig?.LogGroup).toBeDefined();
    }
  });

  test('Lambda 환경 변수에 비밀 값이 없다', () => {
    const text = JSON.stringify(Object.values(t.findResources('AWS::Lambda::Function')).map((f: any) => f.Properties.Environment));
    expect(text).not.toMatch(/githubClientSecret|sessionSigningKey|REPLACE_ME/);
  });

  test('출력에 비밀 값이 없다', () => {
    const outputs = JSON.stringify(t.toJSON().Outputs);
    expect(outputs).not.toMatch(/SecretString|secretsmanager:GetSecretValue/);
  });

  test('Python 3.12 외 런타임 없음', () => {
    for (const f of Object.values(t.findResources('AWS::Lambda::Function')) as any[]) {
      if (/^(shorts|marketing_api)\./.test(f.Properties.Handler)) {
        expect(f.Properties.Runtime).toBe('python3.12');
      }
    }
  });

  test('테이블과 미디어 버킷과 비밀은 RETAIN', () => {
    for (const type of ['AWS::DynamoDB::Table', 'AWS::SecretsManager::Secret']) {
      for (const r of Object.values(t.findResources(type)) as any[]) expect(r.DeletionPolicy).toBe('Retain');
    }
  });
});
