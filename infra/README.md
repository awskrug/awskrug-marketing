# infra

이 서비스의 AWS 리소스를 정의하는 AWS CDK(TypeScript) 앱입니다. 현재 `lib/infra-stack.ts`는 `cdk init`이 만든 빈 스택이며 리소스를 정의하지 않습니다.

## 명령

- `npm run build`: TypeScript를 컴파일합니다.
- `npm run test`: jest 단위 테스트를 실행합니다.
- `npx cdk synth`: CloudFormation 템플릿을 생성합니다.
- `npx cdk diff`: 배포된 스택과 현재 코드의 차이를 보여 줍니다.
- `npx cdk deploy`: 현재 AWS 계정과 리전에 스택을 배포합니다.
