# n8n_agentic_loop_research

> ReAct Research Agent with Gemini & Tavily — n8n + Flask

## Why

검색 → 판단 → 재검색을 스스로 반복하는 ReAct(Reasoning + Acting) 패턴의 AI Agent를 n8n으로 구현했다. 기존 파이프라인은 Model이 한 번 판단하고 끝났지만, 이 프로젝트는 Model이 "결과가 충분한가?"를 스스로 판단해서 부족하면 재검색하고, 충분하면 요약·저장까지 자동으로 마친다.

핵심 아키텍처: 루프 제어는 n8n이, 판단·검색·요약은 Flask 서버 뒤의 Gemini와 Tavily가 담당한다.

## Architecture

```
주제 입력 (curl)
      ↓
n8n Webhook
      ↓
Flask /plan      → Gemini가 첫 번째 검색 키워드 생성
      ↓
Flask /search    → Tavily 웹 검색 + 이전 결과 누적
      ↓
Flask /judge     → Gemini가 "충분한가?" 판단
      ↓
n8n If 노드
  ├─ enough=true            → Flask /summarize → Flask /save → 종료
  └─ enough=false (3회까지) → Flask /search (재검색, 루프백)
```

### 엔드포인트 구성

| 엔드포인트 | 역할 | 입력 | 출력 |
|---|---|---|---|
| `/plan` | 첫 번째 검색 키워드 생성 | `topic` | `keyword`, `topic` |
| `/search` | Tavily 웹 검색 + 결과 누적 | `keyword`, `previous_results` | `results` |
| `/judge` | 결과 충분한지 판단 | `topic`, `results`, `iteration` | `enough`, `next_keyword`, `results` |
| `/summarize` | 전체 결과 마크다운 요약 | `topic`, `all_results` | `summary` |
| `/save` | 마크다운 파일 저장 | `topic`, `summary` | `save_path` |

## Key Design Decisions

**루프 제어를 n8n If 노드 + 루프백으로 구현**
n8n의 Loop Over Items 노드는 입력 아이템을 배치로 받아 전부 처리한 후 종료되는 구조라, 조건에 따라 중간에 멈추는 것이 불가능하다. 대신 `If:false → Search` 로 직접 연결하는 루프백 구조를 사용해, 매 반복마다 Judge의 판단으로 다음 행동(재검색 또는 종료)을 결정하게 했다.

**results 누적을 서버 책임으로 분리**
n8n 표현식만으로 루프 간 데이터를 누적하기 까다로워, `/search` 엔드포인트가 `previous_results`를 받아 새 결과와 합산해서 반환하도록 설계했다. 이렇게 하면 n8n 쪽 워크플로우는 단순하게 유지된다.

**파일 저장을 Flask가 전담**
n8n Docker 컨테이너 내부에서 Write File to Disk 노드가 모든 경로(`/tmp`, 마운트 볼륨 포함)에서 쓰기 권한 오류를 일으켰다. 디버깅 결과 컨테이너 안에서 직접 `touch` 명령은 성공했지만 n8n 노드 자체의 파일 쓰기는 실패하는 것을 확인했고, 더 안정적인 Flask `/save` 엔드포인트로 저장 책임을 옮겼다.

**Gemini 폴백 체인**
무료 티어 요청 제한(429)과 일시 과부하(503)에 대응하기 위해 모델을 순차적으로 시도하는 폴백 체인을 적용했다.

```python
GEMINI_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
]
```

## Tech Stack

| 분류 | 기술 |
|---|---|
| Orchestration | n8n (Docker) |
| Server | Flask |
| LLM | Gemini API (무료 티어) |
| Search | Tavily Search API |
| Language | Python |

## Repository Structure

```
agentic-loop-n8n/
├── server.py          # Flask 서버 — Plan / Search / Judge / Summarize / Save
├── requirements.txt
├── n8n_workflow.json  # n8n 워크플로우 export
└── README.md
```

## How to Run

### 1. 환경 설정

```bash
pip install -r requirements.txt
```

`.env` 파일 생성:
```
GEMINI_API_KEY=발급받은_Gemini_API_키
TAVILY_API_KEY=발급받은_Tavily_API_키
```

### 2. n8n 실행 (Docker)

```bash
docker run -it --rm \
  --name n8n \
  -p 5678:5678 \
  -v ~/.n8n:/home/node/.n8n \
  -e N8N_ENABLE_EXECUTE_COMMAND=true \
  -e NODES_EXCLUDE='[]' \
  n8nio/n8n
```

### 3. Flask 서버 실행

```bash
python3 server.py
# http://localhost:3000
```

### 4. n8n 워크플로우 가져오기

`n8n_workflow.json` 을 n8n 캔버스로 Import 후, Webhook 노드의 Test/Production URL 확인.

### 5. 전체 파이프라인 실행

```bash
curl -X POST http://localhost:5678/webhook-test/research-trigger \
  -H "Content-Type: application/json" \
  -d '{"topic": "LangGraph와 n8n 비교"}'
```

`research_results/` 폴더에 마크다운 보고서가 저장된다.

## Known Issues & Lessons

**Loop Over Items로 조건부 중단 불가능**
n8n Loop Over Items 노드는 입력 아이템을 전부 처리한 후에야 종료되며, 중간에 If 조건으로 멈추는 기능이 없다. 처음에 Plan에서 키워드 3개를 생성해 Loop로 순회시키려 했으나, `enough: true` 가 나와도 나머지 키워드를 계속 처리하는 문제가 발생했다. Loop Over Items를 제거하고 `If:false → Search` 직접 연결 구조로 변경해 해결했다.

**무한루프 방지**
Judge가 계속 `enough: false` 를 반환할 경우를 대비해, If 노드에 `$runIndex >= 2` 조건을 OR로 추가해 최대 3회 반복 후 강제 종료되도록 했다.

<img width="877" height="195" alt="n8n_agentic-loop-research" src="https://github.com/user-attachments/assets/40a47bf2-7ec7-43b8-89ac-48b9f8004a2c" />
