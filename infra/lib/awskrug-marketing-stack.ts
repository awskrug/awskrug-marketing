import * as path from 'path';
import { Construct } from 'constructs';
import {
  CfnOutput,
  Duration,
  Fn,
  RemovalPolicy,
  Stack,
  Token,
  StackProps,
} from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigwv2Auth from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as apigwv2Int from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';

const REPO_ROOT = path.join(__dirname, '..', '..');
const BEDROCK_PROFILE_ID = 'global.anthropic.claude-sonnet-4-6';
// 추론 프로필이 라우팅하는 기반 모델. 모델 ID 접미사(버전 표기)가 달라질 수 있어 와일드카드를 둡니다.
const BEDROCK_FOUNDATION_MODEL = 'anthropic.claude-sonnet-4-6*';
const ASSET_EXCLUDE = ['**/__pycache__', '**/*.pyc', '**/*.egg-info', 'tests', '.pytest_cache'];
// Docker 이미지와 Fargate 작업이 같은 CPU 아키텍처를 쓰도록 한 곳에서 정합니다.
const FARGATE_CPU = ecs.CpuArchitecture.ARM64;
const IMAGE_PLATFORM = ecrAssets.Platform.LINUX_ARM64;

export class AwskrugMarketingStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // ---- 컨텍스트: 세 값이 모두 있을 때만 사용자 도메인을 붙입니다 ----
    const domainName = this.node.tryGetContext('domainName') as string | undefined;
    const hostedZoneId = this.node.tryGetContext('hostedZoneId') as string | undefined;
    const certificateArn = this.node.tryGetContext('certificateArn') as string | undefined;
    const domainValues = [domainName, hostedZoneId, certificateArn];
    const customDomain = domainValues.every((v) => !!v);
    // 일부만 주면 조용히 무시하지 않고 합성을 멈춥니다. 도메인 없이 배포되면 로그인 주소가 어긋납니다.
    if (!customDomain && domainValues.some((v) => !!v)) {
      throw new Error(
        'domainName, hostedZoneId, certificateArn은 세 값을 모두 주거나 모두 생략해야 합니다 ' +
          `(현재 domainName=${domainName ? '있음' : '없음'}, hostedZoneId=${hostedZoneId ? '있음' : '없음'}, ` +
          `certificateArn=${certificateArn ? '있음' : '없음'})`,
      );
    }
    // 호스팅 영역 이름은 선택 값입니다. 없으면 도메인에서 첫 레이블을 뺀 이름으로 봅니다.
    const hostedZoneName =
      (this.node.tryGetContext('hostedZoneName') as string | undefined) ??
      (domainName ? domainName.split('.').slice(1).join('.') : '');
    // 웹 주소(SITE_URL)는 GitHub 로그인의 redirect_uri와 미디어 버킷 CORS 허용 출처에 쓰입니다.
    // 우선순위: 사용자 도메인 > 컨텍스트 siteUrl > 빈 문자열. 사용자 도메인이 없으면 배포 전에는
    // CloudFront 도메인을 알 수 없습니다. 스택이 만든 배포의 도메인을 API 함수 환경 변수에 넣으면
    // 배포 -> API 함수 -> 배포 순환 참조가 생기기 때문입니다. 그래서 처음 배포한 뒤 출력된
    // DistributionDomain을 `-c siteUrl=https://...`로 넘겨 다시 배포합니다.
    const siteUrlContext = (this.node.tryGetContext('siteUrl') as string | undefined)?.trim().replace(/\/+$/, '');
    if (siteUrlContext && !/^https:\/\/[^/\s]+$/.test(siteUrlContext)) {
      throw new Error(`siteUrl은 https://호스트 형식이어야 합니다 (경로 없음): ${siteUrlContext}`);
    }
    const siteUrl = customDomain ? `https://${domainName}` : (siteUrlContext ?? '');

    // ---- 저장소 ----
    const mediaBucket = new s3.Bucket(this, 'MediaBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: RemovalPolicy.RETAIN,
      // 버전 관리는 켜지 않습니다. 원본과 결과물은 30일 뒤 지우는 임시 작업 파일이고,
      // 같은 키를 다시 쓰는 일은 재처리뿐이라 이전 버전을 보관할 이유가 없습니다.
      versioned: false,
      cors: [
        {
          allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.GET, s3.HttpMethods.HEAD],
          // 사용자 도메인도 siteUrl도 없으면 출처를 알 수 없어 모든 출처를 허용합니다.
          // 접근은 미리 서명된 URL로만 가능합니다.
          allowedOrigins: [siteUrl || '*'],
          allowedHeaders: ['*'],
          exposedHeaders: ['ETag'],
          maxAge: 3000,
        },
      ],
      lifecycleRules: [
        { id: 'expire-jobs', prefix: 'jobs/', expiration: Duration.days(30) },
        { id: 'abort-multipart', abortIncompleteMultipartUploadAfter: Duration.days(7) },
      ],
    });

    const webBucket = new s3.Bucket(this, 'WebBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const jobsTable = new dynamodb.Table(this, 'JobsTable', {
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const usersTable = new dynamodb.Table(this, 'UsersTable', {
      partitionKey: { name: 'githubId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const authSecret = new secretsmanager.Secret(this, 'AuthSecret', {
      description: 'GitHub OAuth와 세션 서명 값. 배포 뒤 담당자가 실제 값을 입력합니다.',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({
          githubClientId: 'REPLACE_ME',
          githubClientSecret: 'REPLACE_ME',
        }),
        generateStringKey: 'sessionSigningKey',
        excludePunctuation: true,
        passwordLength: 48,
      },
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const sharedEnv: Record<string, string> = {
      MEDIA_BUCKET: mediaBucket.bucketName,
      JOBS_TABLE: jobsTable.tableName,
      USERS_TABLE: usersTable.tableName,
      SHORTS_BEDROCK_MODEL_ID: BEDROCK_PROFILE_ID,
    };

    // ---- 네트워크와 Fargate ----
    // 가용 영역을 명시해 합성 중 AWS 조회(컨텍스트 공급자)가 일어나지 않게 합니다.
    const vpc = new ec2.Vpc(this, 'Vpc', {
      availabilityZones: ['ap-northeast-2a', 'ap-northeast-2c'],
      natGateways: 0,
      subnetConfiguration: [{ name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 }],
    });
    const cluster = new ecs.Cluster(this, 'Cluster', { vpc });
    const workerSg = new ec2.SecurityGroup(this, 'WorkerSg', { vpc, allowAllOutbound: true });

    const image = new ecrAssets.DockerImageAsset(this, 'WorkerImage', {
      directory: path.join(REPO_ROOT, 'services', 'shorts'),
      platform: IMAGE_PLATFORM,
    });
    const taskDef = new ecs.FargateTaskDefinition(this, 'WorkerTask', {
      cpu: 4096,
      memoryLimitMiB: 8192,
      ephemeralStorageGiB: 50,
      runtimePlatform: {
        cpuArchitecture: FARGATE_CPU,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });
    const workerLogs = new logs.LogGroup(this, 'WorkerLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
    });
    const container = taskDef.addContainer('worker', {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'worker', logGroup: workerLogs }),
      // 글꼴 경로(SHORTS_FONT_FILE, SHORTS_FONT_NAME)는 일부러 덮어쓰지 않습니다.
      // 이미지(services/shorts/Dockerfile)의 ENV가 설치한 글꼴과 짝이 맞습니다.
      environment: { ...sharedEnv },
    });
    // worker 코드가 부르는 호출만 허용합니다. 원본, 전사 결과, 로고 읽기와 음성, 렌더링 결과 쓰기,
    // Jobs 항목 조회와 갱신입니다. ListBucket이 없으면 없는 로고가 404가 아니라 403으로 응답해
    // worker가 "로고 없음"을 구분하지 못합니다.
    taskDef.addToTaskRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject'],
        resources: [mediaBucket.arnForObjects('jobs/*'), mediaBucket.arnForObjects('assets/logo.png')],
      }),
    );
    taskDef.addToTaskRolePolicy(
      new iam.PolicyStatement({ actions: ['s3:PutObject'], resources: [mediaBucket.arnForObjects('jobs/*')] }),
    );
    taskDef.addToTaskRolePolicy(
      new iam.PolicyStatement({ actions: ['s3:ListBucket'], resources: [mediaBucket.bucketArn] }),
    );
    taskDef.addToTaskRolePolicy(
      new iam.PolicyStatement({
        actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
        resources: [jobsTable.tableArn],
      }),
    );

    // ---- 후보 선정 Lambda ----
    const selectFn = new lambda.Function(this, 'SelectCandidatesFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'shorts.handlers.select_candidates',
      code: lambda.Code.fromAsset(path.join(REPO_ROOT, 'services', 'shorts', 'src'), {
        exclude: ASSET_EXCLUDE,
      }),
      timeout: Duration.minutes(5),
      memorySize: 1024,
      environment: sharedEnv,
      logGroup: new logs.LogGroup(this, 'SelectCandidatesLogs', { retention: logs.RetentionDays.ONE_MONTH }),
    });
    selectFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['s3:GetObject'], resources: [mediaBucket.arnForObjects('jobs/*')] }),
    );
    selectFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
        resources: [jobsTable.tableArn],
      }),
    );
    // 글로벌 추론 프로필은 여러 리전의 기반 모델로 라우팅됩니다. 프로필 ARN과 기반 모델 ARN만 허용하고,
    // 기반 모델은 이 프로필을 통해 호출할 때만 허용하도록 조건을 둡니다.
    const profileArn = this.formatArn({
      service: 'bedrock',
      resource: 'inference-profile',
      resourceName: BEDROCK_PROFILE_ID,
    });
    selectFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [profileArn],
      }),
    );
    selectFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [
          `arn:${this.partition}:bedrock:::foundation-model/${BEDROCK_FOUNDATION_MODEL}`,
          `arn:${this.partition}:bedrock:*::foundation-model/${BEDROCK_FOUNDATION_MODEL}`,
        ],
        conditions: { StringLike: { 'bedrock:InferenceProfileArn': profileArn } },
      }),
    );

    // ---- Step Functions: 공통 ----
    const publicSubnets = { subnetType: ec2.SubnetType.PUBLIC };
    const failedValue = tasks.DynamoAttributeValue.fromString('FAILED');
    // 실패 사유는 Jobs 항목의 error 속성에 저장하고 화면에 보입니다. Cause는 Lambda 스택 추적처럼
    // 길어질 수 있으므로 JSONata로 앞 500자만 남깁니다(JSONPath에는 문자열 자르기가 없습니다).
    const errorText = sfn.JsonPath.stringAt('$.errorText');
    const trimError = (id: string, keepRenderId: boolean) =>
      sfn.Pass.jsonata(this, id, {
        outputs: {
          jobId: '{% $states.input.jobId %}',
          ...(keepRenderId ? { renderId: '{% $states.input.renderId %}' } : {}),
          errorText:
            '{% $substring($string($states.input.error.Error) & ": " & $string($states.input.error.Cause), 0, 500) %}',
        },
      });
    const nowText = sfn.JsonPath.stringAt('$$.State.EnteredTime');

    const runWorker = (idPrefix: string, command: string[], timeout: Duration) =>
      new tasks.EcsRunTask(this, `${idPrefix}RunTask`, {
        integrationPattern: sfn.IntegrationPattern.RUN_JOB,
        cluster,
        taskDefinition: taskDef,
        launchTarget: new tasks.EcsFargateLaunchTarget({
          platformVersion: ecs.FargatePlatformVersion.LATEST,
        }),
        assignPublicIp: true,
        subnets: publicSubnets,
        securityGroups: [workerSg],
        containerOverrides: [{ containerDefinition: container, command: Token.asList(sfn.JsonPath.array(...command)) }],
        taskTimeout: sfn.Timeout.duration(timeout),
        resultPath: sfn.JsonPath.DISCARD,
      });

    // ---- 분석 상태 기계 ----
    const analyzeTrim = trimError('AnalyzeTrimError', false);
    const analyzeFailed = new tasks.DynamoUpdateItem(this, 'AnalyzeMarkFailed', {
      table: jobsTable,
      key: { jobId: tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.jobId')) },
      updateExpression: 'SET #s = :s, #e = :e, updatedAt = :u',
      expressionAttributeNames: { '#s': 'status', '#e': 'error' },
      expressionAttributeValues: {
        ':s': failedValue,
        ':e': tasks.DynamoAttributeValue.fromString(errorText),
        ':u': tasks.DynamoAttributeValue.fromString(nowText),
      },
      resultPath: sfn.JsonPath.DISCARD,
    }).next(new sfn.Fail(this, 'AnalyzeFail', { error: 'AnalyzeFailed', cause: 'status FAILED recorded' }));
    analyzeTrim.next(analyzeFailed);

    const extractAudio = runWorker(
      'ExtractAudio',
      ['python', '-m', 'shorts.worker', 'audio', '--job-id', sfn.JsonPath.stringAt('$.jobId')],
      Duration.hours(2),
    );
    const mediaUri = (key: string) =>
      sfn.JsonPath.format(`s3://{}/jobs/{}/${key}`, mediaBucket.bucketName, sfn.JsonPath.stringAt('$.jobId'));
    const transcriptionJobArn = this.formatArn({
      service: 'transcribe',
      resource: 'transcription-job',
      resourceName: 'shorts-*',
    });
    // 같은 jobId로 분석을 다시 실행하면 Transcribe 작업 이름(shorts-{jobId})이 겹쳐 시작이 거부됩니다.
    // 그래서 시작 전에 같은 이름의 이전 작업을 지웁니다. 지울 작업이 없으면 오류가 나므로 무시합니다.
    // Catch의 결과는 $.deleteError에 두어 입력(jobId)을 잃지 않게 합니다.
    const deleteTranscribe = new tasks.CallAwsService(this, 'DeleteOldTranscription', {
      service: 'transcribe',
      action: 'deleteTranscriptionJob',
      parameters: {
        TranscriptionJobName: sfn.JsonPath.format('shorts-{}', sfn.JsonPath.stringAt('$.jobId')),
      },
      iamResources: [transcriptionJobArn],
      resultPath: sfn.JsonPath.DISCARD,
    });
    const startTranscribe = new tasks.CallAwsService(this, 'StartTranscription', {
      service: 'transcribe',
      action: 'startTranscriptionJob',
      parameters: {
        TranscriptionJobName: sfn.JsonPath.format('shorts-{}', sfn.JsonPath.stringAt('$.jobId')),
        LanguageCode: 'ko-KR',
        Media: { MediaFileUri: mediaUri('audio.m4a') },
        OutputBucketName: mediaBucket.bucketName,
        OutputKey: sfn.JsonPath.format('jobs/{}/transcript.json', sfn.JsonPath.stringAt('$.jobId')),
      },
      iamResources: [transcriptionJobArn],
      resultPath: sfn.JsonPath.DISCARD,
    });
    const waitTranscribe = new sfn.Wait(this, 'WaitTranscription', {
      time: sfn.WaitTime.duration(Duration.seconds(20)),
    });
    const getTranscribe = new tasks.CallAwsService(this, 'GetTranscription', {
      service: 'transcribe',
      action: 'getTranscriptionJob',
      parameters: {
        TranscriptionJobName: sfn.JsonPath.format('shorts-{}', sfn.JsonPath.stringAt('$.jobId')),
      },
      iamResources: [transcriptionJobArn],
      resultPath: '$.transcribe',
    });
    const transcribeFailed = sfn.Pass.jsonata(this, 'TranscriptionFailed', {
      outputs: {
        jobId: '{% $states.input.jobId %}',
        error: {
          Error: 'TranscriptionFailed',
          Cause: '{% $string($states.input.transcribe.TranscriptionJob.FailureReason) %}',
        },
      },
    });
    // 전사가 끝나지 않으면 20초 간격으로 최대 360번(2시간) 조회하고, 넘으면 실패로 기록합니다.
    // 상태 기계 전체 제한(5시간)에 걸리면 Jobs 항목이 FAILED로 바뀌지 않고 멈추기 때문입니다.
    const initPoll = new sfn.Pass(this, 'InitPollCount', {
      result: sfn.Result.fromObject({ count: 0 }),
      resultPath: '$.poll',
    });
    const bumpPoll = new sfn.Pass(this, 'BumpPollCount', {
      parameters: { 'count.$': 'States.MathAdd($.poll.count, 1)' },
      resultPath: '$.poll',
    });
    const pollTimedOut = sfn.Pass.jsonata(this, 'TranscriptionTimedOut', {
      outputs: {
        jobId: '{% $states.input.jobId %}',
        error: { Error: 'TranscriptionTimeout', Cause: 'Transcribe 작업이 2시간 안에 끝나지 않았습니다.' },
      },
    });
    const pollLimit = new sfn.Choice(this, 'PollLimitReached?')
      .when(sfn.Condition.numberGreaterThan('$.poll.count', 360), pollTimedOut)
      .otherwise(waitTranscribe);
    const selectTask = new tasks.LambdaInvoke(this, 'SelectCandidates', {
      lambdaFunction: selectFn,
      payload: sfn.TaskInput.fromObject({ jobId: sfn.JsonPath.stringAt('$.jobId') }),
      taskTimeout: sfn.Timeout.duration(Duration.minutes(10)),
      resultPath: sfn.JsonPath.DISCARD,
    });
    const transcribeChoice = new sfn.Choice(this, 'TranscriptionDone?')
      .when(sfn.Condition.stringEquals('$.transcribe.TranscriptionJob.TranscriptionJobStatus', 'COMPLETED'), selectTask)
      .when(sfn.Condition.stringEquals('$.transcribe.TranscriptionJob.TranscriptionJobStatus', 'FAILED'), transcribeFailed)
      .otherwise(bumpPoll.next(pollLimit));

    const catchAll = { errors: ['States.ALL'], resultPath: '$.error' };
    extractAudio.addCatch(analyzeTrim, catchAll);
    deleteTranscribe.addCatch(startTranscribe, { errors: ['States.ALL'], resultPath: '$.deleteError' });
    startTranscribe.addCatch(analyzeTrim, catchAll);
    getTranscribe.addCatch(analyzeTrim, catchAll);
    selectTask.addCatch(analyzeTrim, catchAll);
    transcribeFailed.next(analyzeTrim);
    pollTimedOut.next(analyzeTrim);

    const analyzeMachine = new sfn.StateMachine(this, 'AnalyzeStateMachine', {
      definitionBody: sfn.DefinitionBody.fromChainable(
        extractAudio
          .next(deleteTranscribe)
          .next(startTranscribe)
          .next(initPoll)
          .next(waitTranscribe)
          .next(getTranscribe)
          .next(transcribeChoice),
      ),
      timeout: Duration.hours(5),
    });
    // Transcribe는 호출한 주체(상태 기계 역할)의 권한으로 음성을 읽고 전사 결과를 씁니다.
    analyzeMachine.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['s3:GetObject'], resources: [mediaBucket.arnForObjects('jobs/*')] }),
    );
    analyzeMachine.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['s3:PutObject'], resources: [mediaBucket.arnForObjects('jobs/*')] }),
    );

    // ---- 렌더링 상태 기계 ----
    const renderTrim = trimError('RenderTrimError', true);
    const renderFailed = new tasks.DynamoUpdateItem(this, 'RenderMarkFailed', {
      table: jobsTable,
      key: { jobId: tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.jobId')) },
      updateExpression: 'SET renders.#r.#s = :s, renders.#r.#e = :e, updatedAt = :u',
      expressionAttributeNames: {
        '#r': sfn.JsonPath.stringAt('$.renderId'),
        '#s': 'status',
        '#e': 'error',
      },
      expressionAttributeValues: {
        ':s': failedValue,
        ':e': tasks.DynamoAttributeValue.fromString(errorText),
        ':u': tasks.DynamoAttributeValue.fromString(nowText),
      },
      resultPath: sfn.JsonPath.DISCARD,
    }).next(new sfn.Fail(this, 'RenderFail', { error: 'RenderFailed', cause: 'status FAILED recorded' }));
    renderTrim.next(renderFailed);
    const renderTask = runWorker(
      'Render',
      [
        'python', '-m', 'shorts.worker', 'render',
        '--job-id', sfn.JsonPath.stringAt('$.jobId'),
        '--render-id', sfn.JsonPath.stringAt('$.renderId'),
      ],
      Duration.hours(2),
    );
    renderTask.addCatch(renderTrim, catchAll);
    const renderMachine = new sfn.StateMachine(this, 'RenderStateMachine', {
      definitionBody: sfn.DefinitionBody.fromChainable(renderTask),
      timeout: Duration.hours(3),
    });

    // ---- API Lambda ----
    const apiCode = lambda.Code.fromAsset(path.join(REPO_ROOT, 'services', 'api', 'src'), {
      exclude: ASSET_EXCLUDE,
    });
    const apiEnv: Record<string, string> = {
      ...sharedEnv,
      ANALYZE_STATE_MACHINE_ARN: analyzeMachine.stateMachineArn,
      RENDER_STATE_MACHINE_ARN: renderMachine.stateMachineArn,
      AUTH_SECRET_ARN: authSecret.secretArn,
      GITHUB_ORG: 'awskrug',
      GITHUB_TEAM: 'sns-working-group',
      SITE_URL: siteUrl,
    };
    const apiFn = (fid: string, handler: string, timeout: Duration) =>
      new lambda.Function(this, fid, {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler,
        code: apiCode,
        timeout,
        memorySize: 256,
        environment: apiEnv,
        logGroup: new logs.LogGroup(this, `${fid}Logs`, { retention: logs.RetentionDays.ONE_MONTH }),
      });
    const jobsFn = apiFn('JobsFn', 'marketing_api.jobs.handler', Duration.seconds(29));
    const authFn = apiFn('AuthFn', 'marketing_api.auth.handler', Duration.seconds(15));
    const authorizerFn = apiFn('AuthorizerFn', 'marketing_api.authorizer.handler', Duration.seconds(5));

    // jobs.py: 미리 서명된 URL(읽기, 쓰기)의 서명 권한, head_object, Jobs 항목 조회, 생성, 갱신, 목록(scan).
    // 서명된 URL은 서명한 역할의 권한으로 동작하므로 jobs/ 아래 읽기와 쓰기만 줍니다.
    jobsFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:PutObject'],
        resources: [mediaBucket.arnForObjects('jobs/*')],
      }),
    );
    jobsFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:Scan'],
        resources: [jobsTable.tableArn],
      }),
    );
    jobsFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['dynamodb:GetItem'], resources: [usersTable.tableArn] }),
    );
    analyzeMachine.grantStartExecution(jobsFn);
    renderMachine.grantStartExecution(jobsFn);
    // auth.py는 사용자 항목을 갱신만 합니다.
    authFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem'], resources: [usersTable.tableArn] }),
    );
    authSecret.grantRead(authFn);
    authSecret.grantRead(authorizerFn);

    // ---- HTTP API ----
    const httpApi = new apigwv2.HttpApi(this, 'HttpApi', {
      description: 'AWSKRUG marketing shorts API',
    });
    // 같은 출처(CloudFront)로만 쓰므로 HTTP API에는 CORS를 두지 않습니다. 의도한 구성입니다.
    // 스테이지 기본 제한: 초당 10건, 순간 20건. 소규모 팀 도구에 맞춘 값입니다.
    (httpApi.defaultStage!.node.defaultChild as apigwv2.CfnStage).defaultRouteSettings = {
      throttlingRateLimit: 10,
      throttlingBurstLimit: 20,
    };
    const authorizer = new apigwv2Auth.HttpLambdaAuthorizer('SessionAuthorizer', authorizerFn, {
      responseTypes: [apigwv2Auth.HttpLambdaResponseType.SIMPLE],
      identitySource: ['$request.header.Cookie'],
      resultsCacheTtl: Duration.seconds(0),
    });
    const jobsInt = new apigwv2Int.HttpLambdaIntegration('JobsIntegration', jobsFn);
    const authInt = new apigwv2Int.HttpLambdaIntegration('AuthIntegration', authFn);
    const M = apigwv2.HttpMethod;
    httpApi.addRoutes({ path: '/api/auth/login', methods: [M.GET], integration: authInt });
    httpApi.addRoutes({ path: '/api/auth/callback', methods: [M.GET], integration: authInt });
    httpApi.addRoutes({ path: '/api/auth/logout', methods: [M.POST], integration: authInt, authorizer });
    for (const [method, route] of [
      [M.GET, '/api/me'],
      [M.POST, '/api/jobs'],
      [M.GET, '/api/jobs'],
      [M.GET, '/api/jobs/{jobId}'],
      [M.POST, '/api/jobs/{jobId}/start'],
      [M.POST, '/api/jobs/{jobId}/renders'],
    ] as [apigwv2.HttpMethod, string][]) {
      httpApi.addRoutes({ path: route, methods: [method], integration: jobsInt, authorizer });
    }

    // ---- CloudFront ----
    const apiOrigin = new origins.HttpOrigin(Fn.select(2, Fn.split('/', httpApi.apiEndpoint)), {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
    });
    // 웹 화면은 외부 스크립트와 스타일 없이 같은 출처의 파일만 씁니다. <video>는 미리 서명된 미디어
    // 버킷 주소를, 업로드(XMLHttpRequest PUT)는 같은 버킷 주소를, 아바타는 GitHub 이미지 서버를 씁니다.
    const mediaHost = `https://${mediaBucket.bucketRegionalDomainName}`;
    const securityHeaders = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeaders', {
      securityHeadersBehavior: {
        contentSecurityPolicy: {
          override: true,
          contentSecurityPolicy: [
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self'",
            `img-src 'self' data: https://avatars.githubusercontent.com`,
            `media-src 'self' ${mediaHost}`,
            `connect-src 'self' ${mediaHost}`,
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
          ].join('; '),
        },
        strictTransportSecurity: {
          override: true,
          accessControlMaxAge: Duration.days(365),
          includeSubdomains: true,
        },
        contentTypeOptions: { override: true },
        frameOptions: { override: true, frameOption: cloudfront.HeadersFrameOption.DENY },
        referrerPolicy: {
          override: true,
          referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
        },
      },
    });
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(webBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy: securityHeaders,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: apiOrigin,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
      ...(customDomain
        ? {
            domainNames: [domainName!],
            // 기본 CloudFront 인증서는 TLS 최소 버전을 고를 수 없어 사용자 도메인에서만 지정합니다.
            minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            certificate: acm.Certificate.fromCertificateArn(this, 'SiteCertificate', certificateArn!),
          }
        : {}),
    });

    new s3deploy.BucketDeployment(this, 'WebDeployment', {
      // 개발과 시험 전용 파일은 웹 버킷에 올리지 않습니다.
      sources: [
        s3deploy.Source.asset(path.join(REPO_ROOT, 'web'), {
          exclude: ['README.md', 'dev', 'tests', 'node_modules', 'package.json', 'package-lock.json', '.DS_Store'],
        }),
      ],
      destinationBucket: webBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    if (customDomain) {
      const zone = route53.HostedZone.fromHostedZoneAttributes(this, 'Zone', {
        hostedZoneId: hostedZoneId!,
        zoneName: hostedZoneName,
      });
      const target = route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(distribution));
      new route53.ARecord(this, 'SiteAliasA', { zone, recordName: domainName, target });
      new route53.AaaaRecord(this, 'SiteAliasAaaa', { zone, recordName: domainName, target });
    }

    // ---- 출력 ----
    new CfnOutput(this, 'DistributionDomain', { value: distribution.distributionDomainName });
    new CfnOutput(this, 'ApiUrl', { value: httpApi.apiEndpoint });
    new CfnOutput(this, 'MediaBucketName', { value: mediaBucket.bucketName });
    new CfnOutput(this, 'WebBucketName', { value: webBucket.bucketName });
    new CfnOutput(this, 'AuthSecretArn', { value: authSecret.secretArn });
    new CfnOutput(this, 'AnalyzeStateMachineArn', { value: analyzeMachine.stateMachineArn });
    new CfnOutput(this, 'RenderStateMachineArn', { value: renderMachine.stateMachineArn });
  }
}
