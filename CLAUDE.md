# furiosa-bug-agent — 프로젝트 규칙

> FuriosaAI RNGD 기반 사내 버그 지식 공유 Agent. 숭실대 단기강좌 미니프로젝트.
> 상세 스펙은 `bug_agent_guideline.md` 참조. 이 파일은 **AI 코딩 도구가 매번 읽는 작업 규칙**이다.

## 이 저장소에서 작업할 때 지킬 것

- **한국어로 답한다.**
- **내 담당 파일 외에는 절대 수정하지 않는다.** 4명이 같은 레포를 쓰고 있다. 다른 사람 파일에 문제가 있으면 고치지 말고 **알려만 준다.**
- 새 파일을 만들기 전에 먼저 물어본다.
- API 키를 코드에 하드코딩하지 않는다. 반드시 `.env` + `os.getenv()`.
- 커밋 메시지는 `Add ui.py: ... (D role)` 형식.

---

## 팀 구성과 파일 소유권

| 담당 | 역할 | 소유 파일 |
|---|---|---|
| A | RAG — 임베딩·리랭커·FAISS·코퍼스 | `rag.py`, `bugs.json` |
| B | Vision — 스크린샷 OCR, 통합, API 관리 | `vision.py`, `.env` |
| C | LangGraph — 분석 노드, Tool Calling | `agent.py` |
| **D (나)** | **Streamlit UI · HITL 승인 · LangSmith** | **`ui.py`, `stubs.py`** |

**나는 D 담당이다.** `rag.py` / `vision.py` / `agent.py`는 읽기만 하고 수정하지 않는다.

---

## 아키텍처 (데이터 흐름)

```
스크린샷 업로드
  → [B] extract_error()        이미지에서 에러 텍스트·코드 추출
  → [A] search_bug_corpus()    과거 사례 검색 (임베딩 → 리랭커 2단계)
  → [C] app.invoke()           원인분석 ∥ 후보검토 → 재발판정 → 예방조치
  → [D] ui.py                  화면 표시 → 사람이 승인 (HITL)
  → [A] upsert_bug_case()      승인된 것만 코퍼스에 반영
```

핵심 원칙: **에러를 그 자리에서 고치고 끝내는 게 아니라, 해결 과정을 팀의 검색 가능한 지식으로 축적한다.**
승인(HITL) 전에는 코퍼스에 아무것도 쓰지 않는다.

---

## 인터페이스 계약 (변경 시 팀 전체 합의 필요)

```python
# B 담당 — vision.py  ✅ 완성됨 (저장소에 있음)
def extract_error(image_bytes: bytes, mime_type: str) -> dict:
    """→ {"error_text": str, "code_snippet": str, "raw_ocr_text": str}"""

def normalize_error_input(error_text="", image_bytes=None, mime_type=None, context="") -> dict:
    """★ UI는 이걸 호출한다. 텍스트 / 이미지 / 둘 다를 모두 처리.
    → {"input_type": "text"|"image"|"mixed", "error_text": str,
       "code_snippet": str, "context": str, "raw_ocr_text": str}
    둘 다 비어 있으면 ValueError 발생 → UI에서 반드시 try로 감쌀 것"""

# A 담당 — rag.py
def search_bug_corpus(query: str, top_k: int = 5) -> list[dict]:
    """→ BugCase 리스트. 각 항목에 reranker_score(float) 포함"""

def upsert_bug_case(record: dict, match_type: str, matched_case_id: str | None) -> None:
    """match_type == "confirmed" and matched_case_id → 기존 레코드 occurrence_count += 1
       그 외("possible" | "new") → 새 BugCase로 코퍼스에 추가"""

# C 담당 — agent.py
app.invoke({"error_text": str, "code_snippet": str}) -> dict
    """→ {
        "final_result": {"record": dict, "markdown": str},
        "match_type": "confirmed" | "possible" | "new",
        "matched_case": dict | None,
    }"""
```

### BugCase 스키마

```python
{
  "id": "bug_001",
  "fingerprint": "attributeerror|nonetype-strip|name-field-validation",
  "error_type": "AttributeError",
  "error_message": "'NoneType' object has no attribute 'strip'",
  "environment": "Python 3.11, pandas 2.1.0",
  "context": "CSV로 읽은 사용자 이름 처리 중 발생",
  "root_cause": "...",
  "immediate_fix": "...",
  "prevention_actions": ["...", "..."],
  "external_sources": [],          # web_search를 탄 경우에만 {title, url, accessed_at}
  "status": "monitoring",          # "open" | "monitoring" | "mitigated"
  "occurrence_count": 1,
  "first_seen_at": "2026-08-05T10:00:00",
  "last_seen_at": "2026-08-05T10:00:00",
  "tags": ["pandas", "AttributeError"]
}
```

---

## D 작업 시 반드시 지킬 것 (Streamlit 함정 3개)

Streamlit은 **버튼 클릭·파일 업로드마다 스크립트 전체를 처음부터 다시 실행**한다.
아래 3개를 어기면 반드시 버그가 난다.

1. **분석 결과는 `st.session_state`에 캐싱한다.**
   안 하면 승인 버튼을 누를 때마다 LLM 분석이 처음부터 다시 돌고, 화면에 보여준 것과 저장되는 게 달라진다.

2. **업로드 파일 판별은 파일명이 아니라 내용 해시(`hashlib.sha256`)로 한다.**
   `error.png`라는 같은 이름의 다른 스크린샷을 올리면 옛 결과가 그대로 남는다.

3. **승인 버튼은 저장 후 `disabled=True` 처리한다.**
   두 번 누르면 코퍼스에 중복 저장된다. `upsert_bug_case()`는 시스템에서 유일하게 쓰기가 일어나는 지점이다.

---

## 개발 중 의존성 처리

A/B/C의 실제 코드가 없어도 `stubs.py`로 개발한다. `ui.py` 상단의 import만 바꾸면 통합 완료:

```python
from stubs import normalize_error_input, upsert_bug_case, app
# 통합 시 ↓ 로 교체
# from vision import normalize_error_input   # B — 이미 완성됨
# from rag import upsert_bug_case            # A
# from agent import app                      # C
```

---

## 모델 / 엔드포인트

```python
base_url = "https://endpoint.access.furiosa.dev/v1"
```

### `.env` 변수명 (vision.py 기준 — 반드시 이 이름이어야 함)

```
FURIOSA_VL_API_KEY=<SSU-KEY>
LANGSMITH_API_KEY=<선택>
```

⚠️ `vision.py`는 `os.environ["FURIOSA_VL_API_KEY"]`로 읽는다.
이 변수가 없으면 **import하는 순간 KeyError로 죽는다.**
A·C가 다른 이름을 쓰면 `.env`에 여러 줄을 같이 넣어둘 것.

| 모델 | 담당 | 용도 |
|---|---|---|
| GPT-OSS-120B | C | 원인분석·후보검토(Tool Calling)·재발판정·예방조치 |
| Qwen3-VL-32B-Instruct | B | 스크린샷 OCR |
| Qwen3-Embedding-8B | A | 임베딩 |
| Qwen3-Reranker-8B | A | 리랭킹 |

⚠️ **모델 ID는 가정값이다.** `GET /v1/models`로 실제 ID를 확인한 사람(B)이 확정해서 공유한다.

---

## 데모 시나리오 (이게 통과해야 완성)

1. **1차** — 코퍼스에 없는 새 에러 → `match_type: new` → 승인 → 신규 추가
2. **2차 (핵심)** — 동일 에러 재입력 → `match_type: confirmed` → **"2번째 발생" 경고** → 승인 시 기존 레코드의 `occurrence_count`가 2로 증가
3. **검증** — 메시지는 같지만 원인이 다른 사례가 `possible`로 나오는지 / 승인 버튼 두 번 눌러도 중복 저장 안 되는지

---

## 이번엔 안 하는 것 (Non-goals)

- Slack/Notion 연동, 로그인·인증, 다중 프로젝트 지원
- 예방 조치의 자동 적용·자동 검증 — 제안까지만
- 정교한 fingerprint 매칭 — 단순 문자열 + reranker_score 기반만
- 자동 민감정보 마스킹 — 업로드 전 육안 확인 원칙으로 대체
- 웹 검색 결과의 신뢰도 검증 — 출처만 기록

**스코프를 늘리지 말 것.** 마감은 8/6(목) 21:00, 발표는 8/7(금) 13:00.
