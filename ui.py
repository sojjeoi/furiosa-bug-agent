"""
ui.py — Streamlit 화면 + HITL 승인 (D 담당)

실행:
    streamlit run ui.py

B의 vision.py가 이미 완성돼 있으므로, 실제 OCR을 쓰려면 아래 import에서
normalize_error_input만 vision에서 가져오면 된다. (.env에 FURIOSA_VL_API_KEY 필요)
"""

import hashlib
import os

import streamlit as st
from dotenv import load_dotenv

# --- 의존성 import (통합 완료 — 4개 실제 모듈 사용) --------------------------
from vision import normalize_error_input    # B
from rag import upsert_bug_case             # A
from agent import app                       # C

# from stubs import normalize_error_input, upsert_bug_case, app  # 오프라인 테스트로 되돌리려면 이 줄로 교체
# ---------------------------------------------------------------------------

load_dotenv()

# LangSmith — 환경변수만 켜두면 LangGraph 실행 과정이 자동 기록된다.
# 발표 때 smith.langchain.com 대시보드를 띄우면 Agent Loop를 시각적으로 보여줄 수 있다.
if os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = "furiosa-bug-agent"


# ===========================================================================
# 화면용 마크다운 렌더러
#   저장용 record(dict)와 화면용 문서(str)를 분리한다.
#   ※ C의 aggregator_node가 markdown을 만들어 주면 그쪽을 우선 사용하고,
#     비어 있을 때만 이 함수로 렌더링한다. (소유권은 C와 확정할 것)
# ===========================================================================
def render_document(record: dict, match_type: str, matched_case: dict | None) -> str:
    badge = {
        "confirmed": "🔴 재발 — 동일 원인으로 이미 등록된 사례",
        "possible": "🟡 유사 — 메시지는 비슷하나 원인이 다를 수 있음",
        "new": "🟢 신규 — 코퍼스에 없던 에러",
    }.get(match_type, match_type)

    lines = [
        f"### {badge}",
        "",
        f"**에러**  `{record.get('error_type', '')}`",
        "",
        f"```\n{record.get('error_message', '')}\n```",
    ]

    if record.get("code_snippet"):
        lines += ["", "**문제 코드**", "", f"```python\n{record['code_snippet']}\n```"]

    if record.get("context"):
        lines += ["", f"**상황**  {record['context']}"]

    lines += [
        "",
        "---",
        "",
        "#### 원인 분석",
        record.get("root_cause", "—"),
        "",
        "#### 즉시 조치",
        record.get("immediate_fix", "—"),
        "",
        "#### 예방 조치",
    ]
    actions = record.get("prevention_actions") or []
    lines += [f"- {a}" for a in actions] if actions else ["—"]

    if matched_case:
        lines += [
            "",
            "---",
            "",
            "#### 참고한 과거 사례",
            f"- **{matched_case.get('id', '')}** — {matched_case.get('context', '')}",
            f"- 원인: {matched_case.get('root_cause', '')}",
            f"- 환경: {matched_case.get('environment', '')}",
        ]

    if record.get("external_sources"):
        lines += ["", "#### 외부 출처"]
        lines += [
            f"- [{s.get('title', s.get('url'))}]({s.get('url')})"
            for s in record["external_sources"]
        ]

    return "\n".join(lines)


def make_input_key(error_text: str, image_bytes: bytes | None, context: str) -> str:
    """입력 3종을 합쳐 해시. 파일명이 아니라 '내용'이 바뀌었는지로 판단한다."""
    h = hashlib.sha256()
    h.update(error_text.encode("utf-8"))
    h.update(context.encode("utf-8"))
    if image_bytes:
        h.update(image_bytes)
    return h.hexdigest()


def reset_analysis():
    st.session_state.analysis = None
    st.session_state.extracted = None
    st.session_state.saved = False


# ===========================================================================
# 화면
# ===========================================================================
st.set_page_config(page_title="사내 버그 지식 공유 Agent", page_icon="🐛")

st.title("🐛 사내 버그 지식 공유 Agent")
st.caption(
    "에러를 올리면 과거에 우리 팀이 같은 문제를 겪었는지 찾아 알려줍니다. "
    "승인한 기록만 코퍼스에 쌓입니다."
)

with st.sidebar:
    st.subheader("업로드 전 확인")
    st.markdown(
        "- 스크린샷·에러 텍스트에 **API 키·토큰·내부 경로**가 보이면 가려서 올려주세요.\n"
        "- 자동 마스킹은 이번 스코프에 없습니다."
    )

# --- 입력 -------------------------------------------------------------------
# B의 normalize_error_input()이 텍스트 / 이미지 / 둘 다(mixed)를 모두 처리한다.
st.subheader("1. 에러 입력")

error_text_in = st.text_area(
    "에러 메시지 붙여넣기",
    height=120,
    placeholder="Traceback (most recent call last): ...",
)
uploaded = st.file_uploader("또는 에러 스크린샷 업로드", type=["png", "jpg", "jpeg"])
context_in = st.text_input(
    "상황 설명 (선택)", placeholder="예: CSV로 읽은 사용자 이름 처리 중 발생"
)

image_bytes = uploaded.getvalue() if uploaded else None
mime_type = uploaded.type if uploaded else None

if uploaded:
    st.image(image_bytes, caption="업로드된 스크린샷", use_container_width=True)

has_input = bool(error_text_in.strip()) or image_bytes is not None
input_key = make_input_key(error_text_in, image_bytes, context_in)

# 입력이 바뀌었으면 이전 분석 결과는 무효
if st.session_state.get("input_key") != input_key:
    st.session_state.input_key = input_key
    reset_analysis()

if st.button("분석 시작", type="primary", disabled=not has_input):
    with st.spinner("에러를 읽고 과거 사례를 찾는 중..."):
        try:
            extracted = normalize_error_input(
                error_text=error_text_in.strip(),
                image_bytes=image_bytes,
                mime_type=mime_type,
                context=context_in.strip(),
            )
            st.session_state.extracted = extracted
            st.session_state.analysis = app.invoke(
                {
                    "error_text": extracted["error_text"],
                    "code_snippet": extracted.get("code_snippet", ""),
                    "context": extracted.get("context", ""),
                }
            )
        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"분석 중 오류가 발생했습니다: {e}")

if not has_input:
    st.info("에러 메시지를 붙여넣거나 스크린샷을 올린 뒤 [분석 시작]을 눌러주세요.")

analysis = st.session_state.get("analysis")

# --- 결과 -------------------------------------------------------------------
if analysis:
    st.divider()
    st.subheader("2. 분석 결과")

    record = analysis["final_result"]["record"]
    match_type = analysis["match_type"]
    matched_case = analysis.get("matched_case") or {}
    extracted = st.session_state.get("extracted") or {}

    label = {"text": "텍스트 입력", "image": "스크린샷 OCR", "mixed": "텍스트 + 스크린샷"}
    if extracted.get("input_type"):
        st.caption(f"입력 방식: {label.get(extracted['input_type'], extracted['input_type'])}")

    # 재발 경고 — 데모의 하이라이트
    if match_type == "confirmed":
        count = matched_case.get("occurrence_count", 0) + 1
        st.warning(
            f"이미 등록된 사례와 **동일한 원인**으로 판정됐습니다 — 이번이 **{count}번째 발생**입니다. "
            "근본 원인이 아직 남아있을 수 있습니다."
        )
    elif match_type == "possible":
        st.info("메시지는 비슷하지만 원인이 다를 수 있습니다. 아래 내용을 확인해 주세요.")

    markdown = analysis["final_result"].get("markdown") or render_document(
        record, match_type, analysis.get("matched_case")
    )
    st.markdown(markdown)

    with st.expander("원본 데이터 보기 (저장될 레코드)"):
        st.json(record)
    if extracted.get("raw_ocr_text"):
        with st.expander("OCR 원본 응답"):
            st.text(extracted["raw_ocr_text"])

    # --- 승인 (HITL) --------------------------------------------------------
    # upsert_bug_case()는 이 시스템에서 유일하게 '쓰기'가 일어나는 지점이다.
    st.divider()
    st.subheader("3. 사람 확인 (HITL)")
    st.caption("내용이 맞으면 승인하세요. 승인한 것만 팀 코퍼스에 반영됩니다.")

    if st.session_state.get("saved"):
        st.success("코퍼스에 반영 완료 — 다음에 같은 에러가 나면 즉시 감지됩니다.")
    else:
        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("승인하고 저장", type="primary"):
                upsert_bug_case(
                    record,
                    match_type=match_type,
                    matched_case_id=matched_case.get("id"),
                )
                st.session_state.saved = True
                st.rerun()
        with col2:
            if st.button("다시 분석"):
                reset_analysis()
                st.rerun()
