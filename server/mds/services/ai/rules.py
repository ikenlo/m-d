from __future__ import annotations

from .provider import AIProvider


def _pct(part: int, whole: int) -> int:
    return part * 100 // whole if whole else 0


class RulesProvider(AIProvider):
    name = "rules"

    def recommend(self, facts: dict) -> list[dict]:
        out: list[dict] = []
        rev = facts.get("revenue", 0)
        prof = facts.get("profitability_bp")
        if prof is not None and prof < 1800:
            out.append({"priority": "high", "title": "Низька рентабельність",
                        "text": f"Рентабельність {prof // 100}% — нижче 18%. Перегляньте ціни послуг і найбільші витрати."})
        disc = facts.get("discounts", 0)
        if rev and _pct(disc, rev + disc) >= 10:
            out.append({"priority": "high", "title": "Багато знижок",
                        "text": f"Знижки — {_pct(disc, rev + disc)}% від прайсу. Вони йдуть за рахунок салону: обмежте їх."})
        mat = facts.get("materials", 0)
        if rev and _pct(mat, rev) >= 20:
            out.append({"priority": "medium", "title": "Дорогі матеріали",
                        "text": f"Матеріали — {_pct(mat, rev)}% виручки. Порівняйте ціни постачальників або підніміть ціну «від»."})
        unpaid = facts.get("unpaid_overdue", 0)
        if unpaid:
            out.append({"priority": "high", "title": "Неоплачені замовлення",
                        "text": f"{unpaid} замовлень не оплачено понад 3 дні. Нагадайте клієнтам."})
        noshow = facts.get("cancellations", 0)
        orders = facts.get("orders_count", 0)
        if orders and _pct(noshow, orders + noshow) >= 15:
            out.append({"priority": "medium", "title": "Скасування і неявки",
                        "text": f"{_pct(noshow, orders + noshow)}% записів скасовано або без явки. Підтверджуйте записи напередодні."})
        if facts.get("negative_salon_orders"):
            out.append({"priority": "high", "title": "Замовлення в мінус",
                        "text": f"{facts['negative_salon_orders']} замовлень принесли салону збиток. Перевірте знижки й матеріали."})
        top = facts.get("top_service")
        if top:
            out.append({"priority": "low", "title": "Найпопулярніша послуга",
                        "text": f"«{top}» — лідер періоду. Варто тримати вільні вікна під неї."})
        if not out:
            out.append({"priority": "low", "title": "Усе в нормі", "text": "Показники періоду в межах норми."})
        return out
