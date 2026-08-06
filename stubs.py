"""
stubs.py — A/C 담당 코드가 완성되기 전에 D가 UI를 개발하기 위한 가짜 구현.

B(vision.py)는 이미 완성되어 저장소에 있으므로, 실제 API를 쓰고 싶으면
ui.py에서 vision을 직접 import 하면 된다. (단 .env에 FURIOSA_VL_API_KEY 필요)

사용법:
    ui.py 상단 import를 실제 모듈로 교체하면 통합 끝.
        from vision import normalize_error_input   # B (완성됨)
        from rag import upsert_bug_case            # A
        from agent import app                      # C

이 파일은 D 소유. 실제 구현이 들어와도 삭제하지 말 것 —
import만 되돌리면 언제든 오프라인(무과금) 테스트로 복귀할 수 있다.
"""

from datetime import datetime

# ---------------------------------------------------------------------------
# 데모 시나리오 제어용 스위치
#   "new"       — 코퍼스에 없는 새 에러 (1차 데모)
#   "confirmed" — 같은 원인으로 재발 (2차 데모, 핵심)
#   "possible"  — 메시지는 같지만 원인이 다름
# UI 개발 중 이 값을 바꿔가며 세 화면을 모두 확인할 것.
# ---------------------------------------------------------------------------
DEMO_MATCH_TYPE = "confirmed"


_SAMPLE_CASE = {
    "id": "bug_001",
    "fingerprint": "attributeerror|nonetype-strip|name-field-validation",
    "error_type": "AttributeError",
    "error_message": "'NoneType' object has no attribute 'strip'",
    "environment": "Python 3.11, pandas 2.1.0",
    "context": "CSV로 읽은 사용자 이름 처리 중 발생",
    "root_cause": 'row["name"]에 None이 들어왔는데 문자열 검증 없이 strip() 호출 — 입력 데이터 스키마 검증 부재',
    "immediate_fix": "None 검사 또는 기본값 처리 (df.fillna 또는 조건문)",
    "prevention_actions": [
        "입력 스키마 검증 로직 추가",
        "None 입력에 대한 회귀 테스트 추가",
    ],
    "external_sources": [],
    "status": "monitoring",
    "occurrence_count": 1,
    "first_seen_at": "2026-08-05T10:00:00",
    "last_seen_at": "2026-08-05T10:00:00",
    "tags": ["pandas", "AttributeError", "input-validation"],
    "reranker_score": 0.91,
}

_STUB_ERROR = "AttributeError: 'NoneType' object has no attribute 'strip'"
_STUB_CODE = 'name = row["name"]\nclean_name = name.strip()'


# --- B 담당 (vision.py) — 실제 시그니처와 동일하게 맞춤 ----------------------

def extract_error(image_bytes: bytes, mime_type: str) -> dict:
    """스크린샷 OCR을 흉내낸다."""
    return {
        "error_text": _STUB_ERROR,
        "code_snippet": _STUB_CODE,
        "raw_ocr_text": "(stub) OCR 원본 응답 자리",
    }


def normalize_error_input(
    error_text: str = "",
    image_bytes: bytes | None = None,
    mime_type: str | None = None,
    context: str = "",
) -> dict:
    """텍스트 / 스크린샷 / 둘 다를 공통 형식으로 변환한 척한다.
    실제 vision.py와 동일한 반환 구조:
        {"input_type", "error_text", "code_snippet", "context", "raw_ocr_text"}
    """
    if error_text and not image_bytes:
        return {
            "input_type": "text",
            "error_text": error_text,
            "code_snippet": "",
            "context": context,
            "raw_ocr_text": "",
        }
    if image_bytes:
        ocr = extract_error(image_bytes, mime_type)
        if error_text:
            return {
                "input_type": "mixed",
                "error_text": error_text,
                "code_snippet": ocr["code_snippet"],
                "context": context,
                "raw_ocr_text": ocr["raw_ocr_text"],
            }
        return {
            "input_type": "image",
            "error_text": ocr["error_text"],
            "code_snippet": ocr["code_snippet"],
            "context": context,
            "raw_ocr_text": ocr["raw_ocr_text"],
        }
    raise ValueError("error_text와 image_bytes 중 최소 하나는 있어야 합니다.")


# --- A 담당 (rag.py) --------------------------------------------------------

def search_bug_corpus(query: str, top_k: int = 5) -> list[dict]:
    """유사 사례 검색을 흉내낸다. 각 항목에 reranker_score 포함."""
    if DEMO_MATCH_TYPE == "new":
        return []
    return [_SAMPLE_CASE]


def upsert_bug_case(record: dict, match_type: str, matched_case_id: str | None) -> None:
    """코퍼스 반영을 흉내낸다. 실제 저장 없이 콘솔 출력만."""
    if match_type == "confirmed" and matched_case_id:
        print(f"[stub] UPDATE {matched_case_id} — occurrence_count += 1")
    else:
        print(f"[stub] INSERT 신규 BugCase (match_type={match_type})")


# --- C 담당 (agent.py) ------------------------------------------------------

class _StubApp:
    """LangGraph 컴파일 결과(app)를 흉내낸다. invoke() 하나만 있으면 된다."""

    def invoke(self, state: dict) -> dict:
        error_text = state.get("error_text", "")
        code_snippet = state.get("code_snippet", "")
        context = state.get("context", "")

        matched = _SAMPLE_CASE if DEMO_MATCH_TYPE != "new" else None

        now = datetime.now().isoformat(timespec="seconds")
        record = {
            "id": "bug_new_001",
            "fingerprint": "attributeerror|nonetype-strip|name-field-validation",
            "error_type": "AttributeError",
            "error_message": error_text,
            "environment": "Python 3.11, pandas 2.1.0",
            "context": context or "CSV로 읽은 사용자 이름 처리 중 발생",
            "root_cause": 'row["name"]에 None이 들어왔는데 문자열 검증 없이 strip() 호출',
            "immediate_fix": "None 검사 또는 기본값 처리 (df.fillna 또는 조건문)",
            "prevention_actions": [
                "입력 스키마 검증 로직 추가",
                "None 입력에 대한 회귀 테스트 추가",
            ],
            "external_sources": [],
            "status": "monitoring",
            "occurrence_count": 1,
            "first_seen_at": now,
            "last_seen_at": now,
            "tags": ["pandas", "AttributeError", "input-validation"],
            "code_snippet": code_snippet,
        }

        return {
            "final_result": {"record": record, "markdown": ""},  # markdown은 D가 렌더링
            "match_type": DEMO_MATCH_TYPE,
            "matched_case": matched,
        }


app = _StubApp()
