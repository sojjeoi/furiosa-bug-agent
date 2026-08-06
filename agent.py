"""
C 담당 - Multi-Agent 분석
근본원인 분석(root_cause) + 재발판정(recurrence) + 예방조치(prevention) + 결과조합(aggregator)

의존성 (A 담당 rag.py에서 가져옴):
    build_query(extracted: dict) -> str
    search_bug_corpus(query: str, top_k: int = 5) -> list[dict]   # 각 항목에 reranker_score 포함

upsert_bug_case()는 여기서 호출하지 않는다 — CLAUDE.md 계약상 D 담당 ui.py의 "승인" 버튼에서
직접 호출한다 (HITL 승인 전까지 코퍼스에 아무것도 쓰지 않기 위함).

rag.py가 아직 없어도 이 파일은 import 시점에 에러 없이 로드된다 (노드 실행 시점에만 필요).
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from openai import OpenAI

try:
    from rag import build_query, search_bug_corpus  # A 담당 구현
except ImportError:
    build_query = search_bug_corpus = None

load_dotenv()

LLM_MODEL = "furiosa-ai/gpt-oss-120b"  # 2026-08-06 GET /v1/models로 실제 확인 완료

client = OpenAI(
    base_url="https://endpoint.access.furiosa.dev/v1",
    api_key=os.environ["FURIOSA_LLM_API_KEY"],
)


class AnalysisState(TypedDict, total=False):
    error_text: str
    code_snippet: str
    context: str

    root_cause: str
    candidate_case: dict | None
    retrieval_score: float
    external_sources: list[dict]
    external_evidence: str

    match_type: str
    matched_case: dict | None

    prevention_suggestion: dict
    final_result: dict


# ---------------------------------------------------------------------------
# web_search 도구
#
# 실측 결과 (2026-08-06, furiosa-ai/gpt-oss-120b):
#   - tool_choice="auto"  : 정상 동작
#   - tool_choice="required" 또는 특정 함수 강제 지정 : content=None, tool_calls=[]로
#     응답 자체가 비어버림 (이 서빙 스택에서 깨져 있음) -> 절대 사용하지 말 것
#   - "auto"라도 이미 아는 에러는 모델이 그냥 자체 지식으로 답해버리고 도구를
#     호출하지 않는 경향이 있음 -> 프롬프트에 reranker_score 숫자와
#     "몇 점 미만이면 반드시 호출" 같은 명시적 규칙을 줘야 함
# ---------------------------------------------------------------------------
WEB_SEARCH_TOOL = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "내부 버그 코퍼스에 신뢰할 만한 유사 사례가 없을 때, 이 에러에 대한 공식 문서·알려진 이슈를 웹에서 검색합니다.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "검색할 질의문"}},
            "required": ["query"],
        },
    },
}]


def call_web_search_tool(query: str) -> dict:
    """실제 웹검색 실행부 (DuckDuckGo, ddgs 라이브러리)."""
    from ddgs import DDGS

    results = DDGS().text(query, max_results=1)
    if not results:
        return {"title": "", "url": "", "snippet": "검색 결과 없음"}
    top = results[0]
    return {
        "title": top.get("title", ""),
        "url": top.get("href", ""),
        "snippet": top.get("body", ""),
    }


def _parse_json_block(raw_text: str) -> dict:
    """모델이 JSON 앞뒤로 설명을 붙이는 경우를 대비해 { ... } 구간만 추출."""
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        return json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------
def root_cause_node(state: AnalysisState) -> dict:
    messages = [
        {"role": "system", "content": (
            "당신은 시니어 소프트웨어 엔지니어입니다. 주어진 에러 메시지와 코드를 보고 "
            "근본원인을 2~4문장으로 간결하게 한국어로 설명하세요. 표면적 증상이 아니라 "
            "왜 그 상태가 발생했는지(예: 검증 누락, 잘못된 가정)를 짚어주세요."
        )},
        {"role": "user", "content": (
            f"에러: {state['error_text']}\n"
            f"코드:\n{state.get('code_snippet', '')}\n"
            f"추가 맥락: {state.get('context', '')}"
        )},
    ]
    resp = client.chat.completions.create(model=LLM_MODEL, messages=messages, max_tokens=800)
    return {"root_cause": (resp.choices[0].message.content or "").strip()}


def similar_case_node(state: AnalysisState) -> dict:
    """A 담당의 search_bug_corpus() 결과를 LLM에게 보여주고, LLM이 스스로 판단해서
    신뢰도가 낮으면 web_search 도구를 실제로 호출한다 (2차 호출까지 실행)."""
    if build_query is None or search_bug_corpus is None:
        raise RuntimeError("rag.py의 build_query/search_bug_corpus가 필요합니다 (A 담당 구현 대기 중)")

    query = build_query({"error_text": state["error_text"], "code_snippet": state.get("code_snippet", "")})
    candidates = search_bug_corpus(query)

    messages = [
        {"role": "system", "content": (
            "내부 코퍼스 검색 결과에는 각 후보의 reranker_score(0~1)가 포함되어 있습니다. "
            "규칙: 후보가 없거나, 최상위 후보의 reranker_score가 0.5 미만이면 "
            "반드시 web_search 도구를 호출해 외부 정보를 보강하세요. "
            "reranker_score가 0.5 이상인 후보가 있으면 도구를 호출하지 말고 그 후보를 그대로 신뢰하세요."
        )},
        {"role": "user", "content": (
            f"에러: {state['error_text']}\n"
            f"내부 검색 후보(JSON): {json.dumps(candidates, ensure_ascii=False)}"
        )},
    ]
    resp = client.chat.completions.create(
        model=LLM_MODEL, messages=messages, tools=WEB_SEARCH_TOOL, tool_choice="auto"
    )
    msg = resp.choices[0].message

    if msg.tool_calls:
        tool_call = msg.tool_calls[0]
        args = json.loads(tool_call.function.arguments)
        web_result = call_web_search_tool(args["query"])

        messages.append(msg)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(web_result, ensure_ascii=False),
        })
        final_resp = client.chat.completions.create(model=LLM_MODEL, messages=messages, tools=WEB_SEARCH_TOOL)
        external_evidence = (final_resp.choices[0].message.content or "").strip()

        return {
            "candidate_case": None,
            "retrieval_score": 0.0,
            "external_sources": [{
                "title": web_result["title"],
                "url": web_result["url"],
                "accessed_at": datetime.now(timezone.utc).isoformat(),
            }],
            "external_evidence": external_evidence,
        }

    top = candidates[0] if candidates else None
    return {
        "candidate_case": top,
        "retrieval_score": top["reranker_score"] if top else 0.0,
        "external_sources": [],
        "external_evidence": "",
    }


def _same_root_cause(root_cause_a: str, root_cause_b: str) -> bool:
    # gpt-oss-120b는 reasoning 모델이라 답변 전에 추론 토큰을 먼저 소모한다.
    # max_tokens가 너무 작으면 추론 도중 잘려 content=None(finish_reason=length)이 되므로 여유있게 잡는다.
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{
            "role": "user",
            "content": (
                "다음 두 근본원인 설명이 같은 문제를 가리키면 'yes', 다르면 'no'만 답하세요.\n"
                f"A: {root_cause_a}\nB: {root_cause_b}"
            ),
        }],
        max_tokens=200,
        temperature=0,
    )
    return "yes" in (resp.choices[0].message.content or "").strip().lower()


def recurrence_node(state: AnalysisState) -> dict:
    """root_cause와 candidate_case를 함께 보고 재발 여부 판정.
    score > 0.85 AND root_cause 의미 일치 -> confirmed
    score > 0.5 -> possible
    그 외 -> new
    """
    candidate = state.get("candidate_case")
    score = state.get("retrieval_score", 0.0)

    if not candidate:
        return {"match_type": "new", "matched_case": None}

    # 검색 점수가 매우 높으면(사실상 동일 텍스트) LLM 재확인 없이 바로 confirmed 처리.
    # _same_root_cause()는 temperature=0이어도 reasoning 모델 특성상 완전히 결정적이지
    # 않아서, 완전히 동일한 에러를 재입력하는 데모 시나리오가 가끔 possible로 새는 걸 막는다.
    if score > 0.95:
        match_type = "confirmed"
    elif score > 0.85 and _same_root_cause(state.get("root_cause", ""), candidate.get("root_cause", "")):
        match_type = "confirmed"
    elif score > 0.5:
        match_type = "possible"
    else:
        match_type = "new"

    matched_case = candidate if match_type in ("confirmed", "possible") else None
    return {"match_type": match_type, "matched_case": matched_case}


def prevention_node(state: AnalysisState) -> dict:
    matched_case = state.get("matched_case") or {}
    messages = [
        {"role": "system", "content": (
            "다음 정보를 보고 JSON으로만 답하세요. 다른 설명은 붙이지 마세요.\n"
            '{"immediate_fix": "지금 바로 적용할 수 있는 임시 조치 한 문장", '
            '"prevention_actions": ["재발 방지 조치1", "재발 방지 조치2"], '
            '"tags": ["관련 키워드1", "관련 키워드2"]}'
        )},
        {"role": "user", "content": (
            f"근본원인: {state.get('root_cause', '')}\n"
            f"재발 판정: {state.get('match_type', '')}\n"
            f"기존 유사 사례: {json.dumps(matched_case, ensure_ascii=False)}\n"
            f"외부 검색 근거: {state.get('external_evidence', '')}"
        )},
    ]
    resp = client.chat.completions.create(model=LLM_MODEL, messages=messages, max_tokens=800)
    parsed = _parse_json_block(resp.choices[0].message.content or "")
    return {"prevention_suggestion": {
        "immediate_fix": parsed.get("immediate_fix", ""),
        "prevention_actions": parsed.get("prevention_actions", []),
        "tags": parsed.get("tags", []),
    }}


def _make_fingerprint(error_type: str, error_message: str) -> str:
    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return f"{slug(error_type)}|{slug(error_message)}"


def build_bug_record(extracted: dict, state: AnalysisState) -> dict:
    """저장용 BugCase 형태의 구조화된 dict 반환 (저장용 원본)."""
    error_type, _, error_message = extracted["error_text"].partition(":")
    error_type = error_type.strip() or "UnknownError"
    error_message = error_message.strip() or extracted["error_text"]

    now = datetime.now(timezone.utc).isoformat()
    prevention = state.get("prevention_suggestion", {})

    return {
        "id": None,  # upsert_bug_case가 신규 삽입 시 채움
        "fingerprint": _make_fingerprint(error_type, error_message),
        "error_type": error_type,
        "error_message": error_message,
        "environment": "",
        "context": state.get("context", ""),
        "root_cause": state.get("root_cause", ""),
        "immediate_fix": prevention.get("immediate_fix", ""),
        "prevention_actions": prevention.get("prevention_actions", []),
        "external_sources": state.get("external_sources", []),
        "status": "open",
        "occurrence_count": 1,
        "first_seen_at": now,
        "last_seen_at": now,
        "tags": prevention.get("tags", []),
        "code_snippet": extracted.get("code_snippet", ""),  # BugCase 표준 스키마엔 없지만 ui.py 화면 표시용
    }


def render_document(record: dict, match_type: str, matched_case: dict | None) -> str:
    """record를 발표 화면 표시용 마크다운 문자열로 반환 (표시용, 저장 안 됨)."""
    lines = [f"## {record['error_type']}: {record['error_message']}", ""]

    if match_type == "confirmed" and matched_case:
        lines.append(f"**이미 등록된 사례와 동일한 원인으로 재발** (기존 사례: `{matched_case.get('id')}`)")
    elif match_type == "possible" and matched_case:
        lines.append(f"**기존 사례와 유사할 가능성 있음** (참고 사례: `{matched_case.get('id')}`)")
    else:
        lines.append("**신규 사례**")

    lines += [
        "",
        f"### 근본 원인\n{record['root_cause']}",
        f"### 즉시 조치\n{record['immediate_fix']}",
        "### 재발 방지",
        *[f"- {a}" for a in record["prevention_actions"]],
    ]

    if record["external_sources"]:
        lines.append("### 참고 자료")
        lines += [f"- [{s['title']}]({s['url']})" for s in record["external_sources"]]

    return "\n".join(lines)


def aggregator_node(state: AnalysisState) -> dict:
    extracted = {"error_text": state["error_text"], "code_snippet": state.get("code_snippet", "")}
    record = build_bug_record(extracted, state)
    # markdown은 D(ui.py)가 렌더링을 전담한다 — 여기서는 구조화된 record만 만든다.
    return {"final_result": {"record": record, "markdown": ""}}


# ---------------------------------------------------------------------------
# 그래프 조립
# Fan-out: root_cause + similar_case 병렬 실행 -> Fan-in: recurrence에서 합류
# ---------------------------------------------------------------------------
def build_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("root_cause", root_cause_node)
    graph.add_node("similar_case", similar_case_node)
    graph.add_node("recurrence", recurrence_node)
    graph.add_node("prevention", prevention_node)
    graph.add_node("aggregator", aggregator_node)

    graph.add_edge(START, "root_cause")
    graph.add_edge(START, "similar_case")

    graph.add_edge("root_cause", "recurrence")
    graph.add_edge("similar_case", "recurrence")

    graph.add_edge("recurrence", "prevention")
    graph.add_edge("prevention", "aggregator")
    graph.add_edge("aggregator", END)

    return graph.compile()


app = build_graph()


if __name__ == "__main__":
    # 단독 테스트용 — A 담당의 rag.py 없이도 바로 실행 가능하도록 스텁을 주입한다.
    # A 담당 코드가 준비되면 이 블록은 필요 없어지고 rag.py가 자동으로 import된다.
    _stub_corpus = [{
        "id": "bug_001",
        "error_message": "'NoneType' object has no attribute 'strip'",
        "context": "CSV로 읽은 사용자 이름 처리 중 발생",
        "root_cause": "row[\"name\"]에 None이 들어있는데 문자열 검증 없이 strip() 호출 -> 입력 데이터 스키마 검증 부재",
        "environment": "Python 3.11, pandas 2.1.0",
        "occurrence_count": 1,
        "reranker_score": 0.93,
    }]

    build_query = lambda extracted: extracted["error_text"] + " " + extracted.get("code_snippet", "")
    search_bug_corpus = lambda query, top_k=5: _stub_corpus if "strip" in query else []

    print("=" * 60)
    print("1차 데모: 신규 에러 (코퍼스에 없음)")
    print("=" * 60)
    result_new = app.invoke({
        "error_text": "KeyError: 'user_id'",
        "code_snippet": "session = payload['user_id']",
    })
    print("match_type:", result_new["match_type"])
    print(result_new["final_result"]["markdown"])

    print("\n" + "=" * 60)
    print("2차 데모: bug_001과 동일 에러 재발")
    print("=" * 60)
    result_confirmed = app.invoke({
        "error_text": "AttributeError: 'NoneType' object has no attribute 'strip'",
        "code_snippet": 'name = row["name"]\nclean_name = name.strip()',
    })
    print("match_type:", result_confirmed["match_type"])
    print(result_confirmed["final_result"]["markdown"])
