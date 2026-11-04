# audio-processor

유튜브 영상에서 영어 학습용 구절과 시간 정보를 만드는 Python 처리기입니다. 영상 정보 조회, 오디오 다운로드·변환, Silero VAD, Groq Whisper 전사, 구절 분할을 담당합니다.

DB, 사용자 인증, 작업 큐, 재시도와 진도 저장은 NestJS가 담당합니다. 이 저장소는 별도 컨테이너에서 FastAPI 서버로 실행되며, POST /v1/process 요청 하나를 받아 처리 단계와 결과를 JSONL 스트림으로 반환합니다. 내부 분석은 요청별 Python 프로세스로 격리합니다.

## 실행

```sh
test -f .env || cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/health
```

.env의 GROQ_API_KEY와 YOUTUBE_API_KEY를 설정합니다. 기존 QRUQ_WHISPER, YOUTUBE_DATA_API_v3 이름도 지원합니다. Groq 키는 전사에 필요하며, YouTube 키가 없으면 yt-dlp 메타데이터를 사용합니다. 키는 Compose가 실행 환경에 주입합니다.

PROCESSOR_API_TOKEN은 openssl rand -hex 32로 생성한 뒤 이 저장소와 NestJS 저장소의 .env에 동일하게 설정합니다. 토큰이 32자 미만이면 API 서버는 시작하지 않습니다. PROCESSOR_CONCURRENCY는 동시 처리 개수로 기본 1이며, 초과 요청에는 429와 Retry-After: 15를 반환합니다.

Compose가 graduate-me-processing 네트워크를 만들고 audio-processor라는 내부 이름을 제공합니다. 그 다음 graduate-me 저장소의 Compose를 실행하면 NestJS가 http://audio-processor:8000으로 요청합니다. 이 서버는 DB나 프론트에 직접 연결하지 않습니다. 서버 간 토큰과 음성 API 키는 브라우저에 전달하지 않습니다.

건강 상태는 GET /health, 명세는 /docs에서 확인합니다. POST /v1/process는 Authorization: Bearer 헤더가 필요합니다.

```http
POST /v1/process
Authorization: Bearer 내부_API_토큰
Content-Type: application/json

{"version":1,"operation":"transcribe","youtubeId":"JnfBXjWm7hc"}
```

공개된 15분 이하의 일반 영상을 지원하며 영어 전사를 수행합니다. 라이브·비공개·인증이 필요한 영상은 제외합니다. 외부 영상 추출 제한이나 API 장애는 오류 이벤트로 반환합니다.

## 입출력 계약 v1

HTTP 요청 본문은 아래 JSON입니다.

```json
{ "version": 1, "operation": "transcribe", "youtubeId": "JnfBXjWm7hc" }
```

성공 시 다음 순서로 application/x-ndjson 응답에 JSON 한 줄씩 기록합니다. 모든 이벤트에 `version: 1`이 포함됩니다.

| type | 내용 |
| --- | --- |
| `heartbeat` | 처리 중 10초마다 전송하는 연결 유지 이벤트입니다. |
| `downloaded` | 오디오 원본 다운로드 완료. 호출자가 수신 시각을 저장할 수 있습니다. |
| `transcribing` | `title`, `duration`(초), `category`, `youtubeCategoryId`. 분류 값은 `null`일 수 있습니다. |
| `complete` | `segments`, `speechRegions`, `filterVersion`. 분석 프로세스가 정상 종료한 뒤 반환합니다. |
| `error` | `code`, `message`, `retryable`. 실패한 단계에서 발생합니다. HTTP 200이어도 이 이벤트는 처리 실패입니다. |

`complete` 이벤트 예시:

```json
{
  "version": 1,
  "type": "complete",
  "segments": [
    {
      "index": 0,
      "start": 1.0,
      "end": 2.0,
      "text": "Hello.",
      "words": [{ "word": "Hello.", "start": 1.0, "end": 2.0 }]
    }
  ],
  "speechRegions": [{ "start": 0.8, "end": 2.2 }],
  "filterVersion": 1
}
```

시간은 원본 영상의 시작을 기준으로 한 초 단위입니다. 호출자는 이벤트 형식·순서와 스트림이 정상 종료했는지 검증하고 성공 결과만 저장해야 합니다. 재시도 횟수·지연과 작업 큐는 호출자가 관리하고, 이 서버는 컨테이너당 동시 실행 수를 제한합니다. 이 서버는 15분 타임아웃이나 HTTP 연결 종료 시 하위 프로세스와 임시 파일을 정리합니다. 재시도하면 같은 영상의 외부 API 호출이 다시 발생할 수 있습니다.

`retryable: true`는 일시적인 네트워크·API 장애 등을 뜻합니다. `MISSING_API_KEY`, `STT_AUTH`, `EXTRACTION_BLOCKED`, `NO_SPEECH`, `VAD_FAILED`, `INVALID_TRANSCRIPT` 등은 설정이나 입력을 확인해야 합니다. 말소리가 없으면 Groq API를 호출하지 않습니다.

## 기존 영상 재검사

Groq 전사 없이 VAD 구간만 반환합니다. 기존 구절 제외와 진도 보정은 호출하는 서비스가 수행합니다.

```json
{ "version": 1, "operation": "recheck", "youtubeId": "JnfBXjWm7hc" }
```

응답은 `rechecked` 이벤트 한 개이며 `speechRegions`, `filterVersion`을 포함합니다. HTTP 요청은 서버 파일 경로인 wav 필드를 받지 않습니다. 로컬 WAV를 재사용하는 검사는 내부 CLI main.py에서만 가능합니다.

## 개발

```sh
docker compose -f compose.yaml -f compose.dev.yaml up --build --watch
```

Python 소스 변경은 동기화 후 서버 재시작으로 반영합니다. 의존성이나 Dockerfile 변경은 이미지를 다시 빌드합니다. 개발 재시작 중인 요청은 끊기며 NestJS 작업 관리자가 재시도합니다.

운영에서는 독립적으로 이미지를 빌드·배포하고 NestJS의 PROCESSOR_URL을 해당 주소로 설정합니다. 원격 요청에는 HTTPS 또는 보호된 내부 네트워크를 사용합니다. 스트리밍 프록시는 버퍼링을 끄고 15분 이상의 처리 시간을 허용해야 합니다. Uvicorn은 컨테이너당 프로세스 1개로 실행하고 동시 실행 수는 PROCESSOR_CONCURRENCY로 제어합니다.

## 테스트

```sh
docker compose run --rm -T processor python -m unittest discover -s . -p 'test_*.py'
```

단위·HTTP 인증·요청 검증·동시 실행 제한·연결 취소·프로세스 시간 초과 테스트와 실제 VAD 모델의 무음·합성 음악 검사를 수행합니다. 외부 YouTube·Groq 호출은 테스트에서 대체하므로 음성 API 키나 DB 없이 실행할 수 있습니다. Compose 구성을 읽기 위한 PROCESSOR_API_TOKEN은 .env에 설정해야 합니다. 실제 영상 추출과 전사 품질은 별도 실영상 검증이 필요합니다.

Silero VAD 모델은 이미지 빌드 때 내려받고 SHA-256을 검증합니다. 모델 파일과 비밀키는 Git에 올리지 않습니다. 모델 라이선스는 `licenses/silero-vad.LICENSE`에 있습니다.
