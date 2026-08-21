"""
Как система понимает, что ошиблась:
Через `horizon_hours` (24ч) после создания прогноза мы заново запрашиваем цену
актива, сравниваем с price_at_prediction и с предсказанным направлением.

- actual_direction UP, если цена выросла > +0.5%; DOWN если упала < -0.5%; иначе FLAT
  (небольшой "мертвый диапазон" 0.5%, чтобы шум рынка не считался ошибкой в обе стороны)
- was_correct = predicted.direction == actual_direction
- probability_error = |probability_up - actual_up| — компонент Brier Score,
  накопленная статистика по нему показывает, насколько КАЛИБРОВАНА модель
  (не просто "угадал/не угадал", а насколько корректно она оценивала вероятность).
"""
import datetime as dt

import requests
from django.utils import timezone

from predictor.models import Prediction, PredictionOutcome
from predictor.services.data_fetch import COINGECKO_BASE


def evaluate_due_predictions(assets=None):
    """Проверяет прогнозы, у которых истёк горизонт. assets=None -> по всем
    активам, либо ограничивает проверку переданными Asset (queryset/список)."""
    now = timezone.now()
    due = Prediction.objects.filter(status=Prediction.PENDING).exclude(
        created_at__gt=now - dt.timedelta(hours=1)
    )
    if assets is not None:
        due = due.filter(asset__in=list(assets))
    evaluated = []
    for pred in due:
        deadline = pred.created_at + dt.timedelta(hours=pred.horizon_hours)
        if now < deadline:
            continue
        try:
            resp = requests.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": pred.asset.coingecko_id, "vs_currencies": "usd"},
                timeout=20,
            )
            resp.raise_for_status()
            current_price = resp.json()[pred.asset.coingecko_id]["usd"]
        except Exception:
            continue

        pct_change = (current_price - pred.price_at_prediction) / pred.price_at_prediction * 100
        if pct_change > 0.5:
            actual_direction = Prediction.UP
            actual_up_binary = 1.0
        elif pct_change < -0.5:
            actual_direction = Prediction.DOWN
            actual_up_binary = 0.0
        else:
            actual_direction = Prediction.FLAT
            actual_up_binary = 0.5

        was_correct = actual_direction == pred.direction
        prob_error = abs(pred.probability_up - actual_up_binary)

        PredictionOutcome.objects.create(
            prediction=pred,
            price_at_evaluation=current_price,
            actual_pct_change=pct_change,
            actual_direction=actual_direction,
            was_correct=was_correct,
            probability_error=prob_error,
        )
        pred.status = Prediction.EVALUATED
        pred.save(update_fields=["status"])
        evaluated.append(pred)
    return evaluated


def accuracy_summary():
    """Сводная статистика по проверенным прогнозам — публикуется в API как 'здоровье модели'."""
    outcomes = PredictionOutcome.objects.all()
    total = outcomes.count()
    if total == 0:
        return {"total_evaluated": 0, "hit_rate": None, "avg_brier_component": None}
    correct = outcomes.filter(was_correct=True).count()
    avg_error = sum(o.probability_error for o in outcomes) / total
    return {
        "total_evaluated": total,
        "hit_rate": round(correct / total, 3),
        "avg_brier_component": round(avg_error, 3),
    }