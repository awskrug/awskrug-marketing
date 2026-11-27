#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AwskrugMarketingStack } from '../lib/awskrug-marketing-stack';

const app = new cdk.App();
new AwskrugMarketingStack(app, 'AwskrugMarketingStack', {
  // 리전은 서울로 고정합니다. 계정은 현재 AWS CLI 설정에서 받으며 저장소에 적지 않습니다.
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'ap-northeast-2' },
});
