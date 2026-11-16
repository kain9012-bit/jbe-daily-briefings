"""OpenRouter 호출. edu-news-alert의 harness/openrouter_client.py를 이 저장소용으로 줄인 것.

키는 환경변수 OPENROUTER_API_KEY로만 받는다. 모델은 LLM_MODEL로 바꿀 수 있다.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.5-flash"


class OpenRouterError(RuntimeError):
    pass


def parse_json_response(text: str) -> Any:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise OpenRouterError(f"JSON 해석 실패: {cleaned[:300]}")
        return json.loads(cleaned[start : end + 1])


class OpenRouterClient:
    JSON_ONLY = "\n\n반드시 JSON 값 하나만 출력하세요. 설명·머리말·코드펜스를 붙이지 마세요."
    MAX_TOKENS_CEILING = 32768

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, max_output_tokens: int = 8192, timeout: int = 240) -> None:
        if not (api_key or "").strip():
            raise OpenRouterError("OPENROUTER_API_KEY가 설정되지 않았습니다.")
        self.api_key = api_key.strip()
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.served_model = ""
        self.last_finish_reason = ""
        self.usage = {"requests": 0, "prompt": 0, "completion": 0, "total": 0}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/kain9012-bit/jbe-daily-briefings",
            "X-Title": "jbe-daily-briefings",
        }

    def _post(self, body: dict[str, Any]) -> requests.Response:
        return requests.post(f"{API_BASE}/chat/completions", headers=self._headers(), json=body, timeout=self.timeout)

    def _read(self, response: requests.Response) -> str:
        if not response.ok:
            raise OpenRouterError(f"OpenRouter 요청 실패 ({response.status_code}): {response.text[:500]}")
        payload = response.json()
        if payload.get("error"):
            raise OpenRouterError(f"OpenRouter 오류: {json.dumps(payload['error'], ensure_ascii=False)[:300]}")
        self.served_model = str(payload.get("model") or "")
        usage = payload.get("usage") or {}
        self.usage["requests"] += 1
        self.usage["prompt"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion"] += int(usage.get("completion_tokens") or 0)
        self.usage["total"] += int(usage.get("total_tokens") or 0)
        choices = payload.get("choices") or []
        if not choices:
            raise OpenRouterError(f"응답 후보가 없습니다: {str(payload)[:300]}")
        self.last_finish_reason = str(choices[0].get("finish_reason") or "")
        text = str((choices[0].get("message") or {}).get("content") or "")
        if not text.strip():
            raise OpenRouterError(f"응답에 텍스트가 없습니다 (finish_reason={self.last_finish_reason}).")
        return text

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> Any:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
        }
        response = self._post(body)
        # 스키마를 못 받는 모델이면 스키마를 빼고 다시 부른다.
        if not response.ok and response.status_code in (400, 404, 422):
            body["response_format"] = {"type": "json_object"}
            body["messages"][1]["content"] = prompt + self.JSON_ONLY
            response = self._post(body)
        text = self._read(response)
        try:
            return parse_json_response(text)
        except Exception:
            pass
        if self.last_finish_reason == "length":
            body["max_tokens"] = min(self.MAX_TOKENS_CEILING, int(body["max_tokens"]) * 2)
        body["messages"][1]["content"] = prompt + self.JSON_ONLY
        return parse_json_response(self._read(self._post(body)))
