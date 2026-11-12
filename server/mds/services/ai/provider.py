from __future__ import annotations

from abc import ABC, abstractmethod


class AIError(Exception):
    pass


class AIProvider(ABC):
    name = "abstract"

    @abstractmethod
    def recommend(self, facts: dict) -> list[dict]:
        """facts — лише агрегати (без імен і телефонів). Повертає [{title, text, priority}]."""
