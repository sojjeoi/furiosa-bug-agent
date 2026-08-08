# agent.py — C 담당 (Multi-Agent 분석)

`bug_agent_guideline.md`의 C 역할(근본원인 분석 → 재발판정 → 예방조치 → 결과조합)을 LangGraph로 구현한 모듈.

## 이 모듈이 하는 일

```
START ─┬─ root_cause_node   (에러+코드 → 근본원인 텍스트)
       └─ similar_case_node (A 담당 코퍼스 검색 → 후보 확신 낮으면 web_search 도구 실제 호출)
              │
              ▼ (둘 다 끝나야 진행 — fan-in)
        recurrence_node     (근본원인 + 후보를 같이 보고 confirmed/possible/new 판정)
              ▼
        prevention_node     (판정 결과 보고 즉시조치·재발방지 생성)
              ▼
        aggregator_node     (BugRecord dict + 화면용 markdown 조립)
              ▼
             END
```

`app = build_graph()` 를 만들어두었고, 실행은 `app.invoke({"error_text": ..., "code_snippet": ..., "context": ...})`.
반환값은 `AnalysisState` 전체(dict)이며, 그중 `final_result["record"]`가 저장용, `final_result["markdown"]`이 화면 표시용이다.

## 의존성 — A 담당 `rag.py`

이 모듈이 실제로 `from rag import ...`로 가져오는 함수는 아래 2개뿐이다. 아직 `rag.py`가 없어도 import 에러 없이 로드되며(노드 실행 시점에만 필요), 없는 상태로 그래프를 실행하면 `similar_case_node`에서 `RuntimeError`가 발생한다.

| 함수 | 시그니처 | 비고 |
|---|---|---|
| `build_query` | `(extracted: dict) -> str` | `extracted = {"error_text": ..., "code_snippet": ...}` |
| `search_bug_corpus` | `(query: str, top_k: int = 5) -> list[dict]` | 각 항목에 `reranker_score`(0~1) 필수 |

`upsert_bug_case(record, match_type, matched_case_id)`는 이 모듈에서 호출하지 않는다. CLAUDE.md 계약상 승인(HITL) 전엔 코퍼스에 아무것도 쓰면 안 되므로, D 담당 `ui.py`의 "승인하고 저장" 버튼에서 직접 호출한다.

`build_bug_record` / `render_document`는 `ui.py` 확인 결과 소유권이 정리됐다 — C(`agent.py`)의 `aggregator_node`가 만든 `final_result["markdown"]`을 D가 우선 사용하고, 비어 있을 때만 `ui.py` 자체 폴백 렌더러를 쓴다. 그래서 계속 이 파일에 둔다.

## 환경변수

`.env`에 `FURIOSA_LLM_API_KEY` 필요 (GPT-OSS-120B 전용 키, VL 모델 키와 다름).

## 단독 테스트

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 agent.py
```

`rag.py` 없이도 `__main__` 블록의 스텁 코퍼스(`bug_001` 1건)로 두 시나리오를 바로 확인 가능:
1. 코퍼스에 없는 새 에러 → `match_type: new` (실제 web_search 호출됨, 네트워크 필요)
2. `bug_001`과 동일 에러 재발 → `match_type: confirmed`

## 실측으로 확인된 제약사항 (2026-08-06, `furiosa-ai/gpt-oss-120b` 기준)

이 엔드포인트/모델 특성상 아래 두 가지를 모르면 애매하게 침묵 실패(빈 응답)가 난다. RAG나 UI 쪽에서 같은 모델로 별도 LLM 호출을 추가할 때도 해당됨.

1. **`tool_choice`를 강제하면 안 됨.** `tool_choice="required"`나 `{"type": "function", "function": {"name": ...}}`처럼 특정 함수를 강제 지정하면 `content=None`, `tool_calls=[]`로 응답이 통째로 빈 채 온다. 반드시 `tool_choice="auto"`만 사용하고, 도구 호출을 유도하고 싶으면 시스템 프롬프트 문구로 강제해야 한다 (예: "점수가 X 미만이면 반드시 호출하세요").
2. **`gpt-oss-120b`는 reasoning 모델이라 답변 전에 추론 토큰을 먼저 소모한다.** `max_tokens`를 짧게(예: 5, 몇백 이하) 잡으면 추론 도중 잘려서 `finish_reason="length"`, `content=None`이 되거나 JSON이 중간에 끊긴다. 짧은 yes/no 답변을 기대하는 호출에도 `max_tokens`는 넉넉히(200 이상) 잡아야 한다. `agent.py`에서는 구조화 출력 호출을 800, 판정용 호출을 200으로 설정해둠.

## A 담당에게: `reranker_score` 관련 확인 (2026-08-06 실측)

`recurrence_node`의 임계값(0.85=confirmed, 0.5=possible)은 **점수가 0~1 범위라는 가정** 하에 정한 값이다. `/v1/rerank`를 직접 호출해서 실측한 결과:

```
관련 있는 문서 → relevance_score: 0.8807970285415649
관련 없는 문서 → relevance_score: 0.0000022603242086915998
```

0~1 범위가 맞아서 임계값은 그대로 써도 된다. 다만 **API 응답 필드명은 `relevance_score`이지 `reranker_score`가 아니다** — `search_bug_corpus()`가 반환하는 dict에는 계약대로 `reranker_score`라는 키로 매핑해서 넣어줘야 `agent.py`가 그대로 읽는다.

## 알려진 한계 / TODO

- `_same_root_cause`(재발 판정 시 의미 비교)는 LLM 1회 호출로만 판단 — 실측상 정확도는 괜찮았지만 별도 검증은 안 함.
- `render_document`의 markdown 포맷은 D 담당 Streamlit 화면에 맞춰 조정이 필요할 수 있음.
- `build_bug_record`의 `environment` 필드는 현재 항상 빈 문자열 — `AnalysisState`에 해당 정보를 담을 필드가 없어서. 필요하면 B 담당 OCR 결과나 사용자 입력에서 받아와야 함.
- `record`에 `code_snippet` 필드를 추가함 (BugCase 표준 스키마엔 없음) — `ui.py`/`stubs.py`가 화면에 "문제 코드"를 표시할 때 이 키를 찾길래 맞춰줌. A가 `bugs.json`에 그대로 저장할지, 저장 전에 제거할지는 A와 확인 필요.
