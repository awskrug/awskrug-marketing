# 쇼츠 처리 작업 이미지. 빌드 컨텍스트는 services/shorts 디렉터리다.
FROM python:3.12-slim

# Debian의 ffmpeg 패키지는 libass(자막)와 freetype(drawtext)을 포함한다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# drawtext는 글꼴 파일 경로가 필요하다. .ttc의 첫 번째 얼굴(JP)도 한글 글리프를 포함한다.
ENV SHORTS_FONT_FILE=/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc \
    SHORTS_FONT_NAME="Noto Sans CJK KR" \
    PYTHONUNBUFFERED=1

# 기본 실행은 사용법만 출력한다. 작업 정의가 command를 덮어쓴다.
CMD ["python", "-m", "shorts.worker", "--help"]
