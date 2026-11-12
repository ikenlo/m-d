from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .provider import AIError, AIProvider

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "financial_analysis.md"


class LocalLlamaProvider(AIProvider):
    name = "llama"

    def __init__(self, url: str = "http://localhost:8080/v1", timeout: float = 30.0, model: str = "qwen2.5"):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.model = model

    def recommend(self, facts: dict) -> list[dict]:
        body = {"model": self.model, "temperature": 0.2,
                "messages": [{"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
                             {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]}
        req = urllib.request.Request(self.url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                data = json.loads(r.read().decode())
            text = data["choices"][0]["message"]["content"]
            start, end = text.find("["), text.rfind("]")
            items = json.loads(text[start:end + 1])
        except Exception as e:  # noqa: BLE001
            raise AIError(str(e)) from e
        out = [{"priority": str(x.get("priority", "medium")), "title": str(x.get("title", ""))[:120],
                "text": str(x.get("text", ""))[:600]} for x in items if isinstance(x, dict) and x.get("text")]
        if not out:
            raise AIError("порожня відповідь")
        return out
