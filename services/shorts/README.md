# shorts

밋업 녹화 영상 한 편을 로컬에서 전사하고, 쇼츠로 쓸 구간 후보를 고르고, 고른 구간을 세로(1080x1920) 영상으로 렌더링하는 명령줄 도구입니다. 한 후보에서 최대 세 가지 화면 구성(split, blur, crop)의 mp4가 나오고, 각 영상 끝에는 2초짜리 끝 화면이 붙습니다.

설계 배경은 [docs/mvp-shorts.md](../../docs/mvp-shorts.md)와 [docs/architecture.md](../../docs/architecture.md)에 있습니다.

## 필요한 도구

- Python 3.12 이상. 설치는 이 디렉토리에서 `pip install -e ".[test]"`를 실행합니다. 설치하면 `shorts` 명령이 생기고, 설치하지 않았다면 `PYTHONPATH=src python -m shorts`로 실행할 수 있습니다.
- FFmpeg 7. libass(자막)와 drawtext(제목)를 켜서 빌드한 것이어야 합니다. `ffmpeg -filters | grep -E "ass|drawtext"`로 확인합니다.
- 한글을 지원하는 글꼴 파일(예: Pretendard). 저장소에는 글꼴과 로고를 넣지 않습니다. `render` 명령의 `--font-file` 또는 환경 변수 `SHORTS_FONT_FILE`로 경로를 지정합니다.
- Transcribe, S3, Bedrock을 쓸 수 있는 AWS 프로필. `transcribe`와 `candidates`에만 필요합니다. 프로필은 `--profile`로 지정하며, 지정하지 않으면 `AWS_PROFILE` 환경 변수를 따릅니다.

## 비용 주의

`transcribe`는 영상을 S3에 올리고 Amazon Transcribe 작업을 실행하므로 S3 저장과 전사 요금이 발생합니다. `candidates`는 전사문 전체를 Bedrock 모델에 보내므로 토큰 요금이 발생합니다. 두 명령은 실행 전에 확인을 묻지 않습니다. `candidates` 결과 파일의 `usage`에 입력·출력 토큰 수가 기록됩니다. `frame`과 `render`는 로컬에서만 동작하며 AWS를 호출하지 않습니다.

## 사용 순서

공통 옵션은 `--profile`과 `--region`(기본 `ap-northeast-2`)입니다. 오류가 나면 stderr에 한 줄을 쓰고 종료 코드 2로 끝납니다.

### 1. transcribe: 영상 전사

```bash
shorts transcribe meetup.mp4 --bucket my-work-bucket --out transcript.json --profile my-profile
```

영상을 S3에 올리고 한국어(ko-KR)로 전사한 결과 JSON을 `--out`에 저장합니다. 작업 이름은 `--job-name`으로 정하며, 생략하면 `shorts-`에 UTC 시각을 붙여 만듭니다.

### 2. candidates: 구간 후보 선정

```bash
shorts candidates transcript.json --out candidates.json --count 8 --profile my-profile
```

전사문을 문장으로 나눈 뒤 Bedrock 모델에게 30-60초 길이의 후보를 최대 `--count`개(기본 8) 고르게 합니다. 모델 ID는 환경 변수 `SHORTS_BEDROCK_MODEL_ID`로 바꿀 수 있고, 기본값은 `global.anthropic.claude-sonnet-4-6`입니다. 결과 파일에는 `model_id`, `usage`(`input_tokens`, `output_tokens`), `candidates`가 들어갑니다.

### 3. frame: 영역을 정하기 위한 프레임 추출

```bash
shorts frame meetup.mp4 --at 754.5 --out frame.png
```

`--at` 초 지점의 프레임 한 장을 PNG로 저장합니다. 객석에서 찍은 밋업 영상은 구간마다 카메라 위치와 줌이 달라서, 후보마다 발표 화면과 발표자 영역을 사람이 지정해야 합니다. 후보 구간 안의 시각으로 프레임을 뽑아 이미지 뷰어에서 해당 영역의 왼쪽 위 좌표와 폭, 높이를 원본 픽셀 단위로 읽습니다. 영역은 `x,y,w,h` 형식입니다. 예를 들어 1920x1080 원본에서 오른쪽 위 절반이 슬라이드라면 `960,0,960,540`입니다.

### 4. render: 렌더링

```bash
shorts render meetup.mp4 transcript.json candidates.json --index 0 --out-dir out \
  --font-file /path/to/Pretendard-Bold.ttf \
  --slide 960,0,960,540 --speaker 100,200,500,700 --logo logo.png
```

`candidates.json`의 `--index`번째(0부터) 후보를 렌더링하고, 만든 파일 경로를 한 줄씩 출력합니다. `--slide`와 `--speaker`를 둘 다 주면 위아래로 나눈 split 구성이 추가되고, 하나라도 빠지면 split은 건너뛰고 blur와 crop만 만듭니다. `--logo`는 선택이며, 주면 영상 오른쪽 아래와 끝 화면에 로고가 들어갑니다. 출력 파일 이름은 `clip-{시작 초}-{구성}.mp4`입니다.

## 테스트

저장소 루트에서 `pytest`를 실행합니다. FFmpeg를 실행하는 테스트는 `slow` 마커가 붙어 있고 글꼴이 필요하므로, `SHORTS_FONT_FILE`을 지정한 환경에서만 실행됩니다. 나머지 테스트는 AWS와 네트워크를 호출하지 않습니다.
