# audio-processor

유튜브 영상에서 영어 학습용 구절과 시간 정보를 만드는 Python 처리기입니다. 영상 정보 조회, 오디오 다운로드·변환, Silero VAD, Groq Whisper 전사, 구절 분할을 담당합니다.

DB, 사용자 인증, 작업 큐, 재시도와 진도 저장은 호출하는 서비스가 담당합니다. 이 프로그램은 HTTP 서버가 아니며, 표준 입력으로 요청 한 개를 받고 표준 출력으로 JSONL 이벤트를 반환한 뒤 종료합니다. Python 3.12, Node.js 22, FFmpeg, 의존 패키지는 Docker 이미지에 포함됩니다.

## 실행

이 디렉터리만으로 빌드할 수 있습니다. 별도 저장소에서는 이 디렉터리의 내용이 저장소 루트가 됩니다.

```sh
test -f .env || cp .env.example .env
docker compose build
```

`.env`의 `GROQ_API_KEY`와 `YOUTUBE_API_KEY`를 설정합니다. Groq 키는 전사에 필요합니다. YouTube 키가 없으면 yt-dlp 메타데이터를 사용합니다. 키는 Compose가 실행 환경에 주입하며 소스와 이미지에는 포함하지 않습니다.

```sh
printf '%s\n' '{"version":1,"operation":"transcribe","youtubeId":"JnfBXjWm7hc"}' | docker compose run --rm -T processor
```

공개된 15분 이하의 일반 영상을 지원하며 영어 전사를 수행합니다. 라이브·비공개·인증이 필요한 영상은 제외합니다. 외부 영상 추출 제한이나 API 장애가 발생하면 오류 이벤트를 반환합니다.

## 입출력 계약 v1

표준 입력은 아래 JSON 한 줄입니다.

```json
{ "version": 1, "operation": "transcribe", "youtubeId": "JnfBXjWm7hc" }
```

성공 시 다음 순서로 표준 출력에 JSON 한 줄씩 기록합니다. 모든 이벤트에 `version: 1`이 포함됩니다.

| type | 내용 |
| --- | --- |
| `downloaded` | 오디오 원본 다운로드 완료. 호출자가 수신 시각을 저장할 수 있습니다. |
| `transcribing` | `title`, `duration`(초), `category`, `youtubeCategoryId`. 분류 값은 `null`일 수 있습니다. |
| `complete` | `segments`, `speechRegions`, `filterVersion`. 처리 성공 후 종료 코드 0을 반환합니다. |
| `error` | `code`, `message`, `retryable`. 실패한 단계에서 발생하며 종료 코드 1을 반환합니다. |

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

시간은 원본 영상의 시작을 기준으로 한 초 단위입니다. 호출자는 이벤트 형식·순서와 종료 코드를 검증하고 성공 결과만 저장해야 합니다. 재시도 횟수·지연, 동시 작업 제한, 타임아웃과 하위 프로세스 종료는 호출자가 관리합니다. 재시도하면 같은 영상의 외부 API 호출이 다시 발생할 수 있습니다.

`retryable: true`는 일시적인 네트워크·API 장애 등을 뜻합니다. `MISSING_API_KEY`, `STT_AUTH`, `EXTRACTION_BLOCKED`, `NO_SPEECH`, `VAD_FAILED`, `INVALID_TRANSCRIPT` 등은 설정이나 입력을 확인해야 합니다. 말소리가 없으면 Groq API를 호출하지 않습니다.

## 기존 영상 재검사

Groq 전사 없이 VAD 구간만 반환합니다. 기존 구절 제외와 진도 보정은 호출하는 서비스가 수행합니다.

```json
{ "version": 1, "operation": "recheck", "youtubeId": "JnfBXjWm7hc" }
```

응답은 `rechecked` 이벤트 한 개이며 `speechRegions`, `filterVersion`을 포함합니다. 동일 영상에서 추출한 16kHz 모노 16비트 PCM 파일이 있으면 요청의 `wav`에 실행 환경 안의 파일 경로를 지정할 수 있습니다. 이 옵션은 신뢰하는 내부 호출자만 사용해야 합니다.

## 테스트

```sh
docker compose run --rm -T processor python -m unittest discover -s . -p 'test_*.py'
```

단위·처리 계약 테스트와 실제 VAD 모델의 무음·합성 음악 검사를 수행합니다. 외부 YouTube·Groq 호출은 테스트에서 대체하므로 API 키나 DB 없이 실행할 수 있습니다. 실제 영상 추출과 전사 품질은 별도 실영상 검증이 필요합니다.

Silero VAD 모델은 이미지 빌드 때 내려받고 SHA-256을 검증합니다. 모델 파일과 비밀키는 Git에 올리지 않습니다. 모델 라이선스는 `licenses/silero-vad.LICENSE`에 있습니다.
