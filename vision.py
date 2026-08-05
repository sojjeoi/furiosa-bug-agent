"""
B 담당 - 화면 인식 (Vision/OCR)
에러 스크린샷에서 에러 메시지와 코드를 추출한다.
"""
import base64
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

VL_MODEL = "furiosa-ai/Qwen3-VL-32B-Instruct"

client = OpenAI(
    base_url="https://endpoint.access.furiosa.dev/v1",
    api_key=os.environ["FURIOSA_VL_API_KEY"],
)

OCR_PROMPT = (
    "이 스크린샷은 개발 중 발생한 에러 화면입니다. "
    "다음 형식의 JSON으로만 답하세요. 다른 설명은 붙이지 마세요.\n"
    '{"error_text": "에러 메시지 전문", "code_snippet": "화면에 보이는 코드가 있으면 그대로, 없으면 빈 문자열"}'
)


def extract_error(image_bytes: bytes, mime_type: str) -> dict:
    """스크린샷에서 ExtractedError 딕셔너리 반환.

    Returns:
        {"error_text": str, "code_snippet": str, "raw_ocr_text": str}
    """
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_url = f"data:{mime_type};base64,{image_b64}"

    resp = client.chat.completions.create(
        model=VL_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
        max_tokens=512,
    )

    raw_text = resp.choices[0].message.content or ""

    try:
        # 모델이 JSON 앞뒤로 부연설명을 붙이는 경우 대비, { ... } 구간만 추출
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        parsed = json.loads(raw_text[start:end])
        error_text = parsed.get("error_text", "").strip()
        code_snippet = parsed.get("code_snippet", "").strip()
    except (ValueError, json.JSONDecodeError):
        # JSON 파싱 실패해도 파이프라인이 끊기지 않도록 원본 텍스트를 그대로 사용
        error_text = raw_text.strip()
        code_snippet = ""

    return {
        "error_text": error_text,
        "code_snippet": code_snippet,
        "raw_ocr_text": raw_text,
    }


if __name__ == "__main__":
    # 단독 테스트용 — A/C/D 담당 코드 없이도 바로 실행 가능
    # 프로젝트 루트에 test_error_screenshot.png 를 넣고 실행하면 됨
    test_image_path = "test_error_screenshot.png"
    with open(test_image_path, "rb") as f:
        image_bytes = f.read()

    result = extract_error(image_bytes, mime_type="image/png")
    print(json.dumps(result, ensure_ascii=False, indent=2))
