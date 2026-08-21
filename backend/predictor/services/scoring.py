"""
АЛГОРИТМ ПРОГНОЗА (простая объяснимая weighted-scoring модель, без "чёрного ящика" ML).

Вход: последние NormalizedMetric по активу.
  m24  = momentum_24h        (%, изменение цены за 24ч)
  m7   = momentum_7d         (%, изменение цены за 7д)
  vc   = volume_change_24h   (%, изменение объёма торгов)
  vol7 = volatility_7d       (%, std дневных доходностей - используется только как РИСК)
  sent = news_sentiment_avg  (-1..+1, тональность новостей)
  nvol = news_volume_48h     (шт., кол-во новостей - влияет на УВЕРЕННОСТЬ, не на направление)

Шаг 1. Каждый сигнал приводится к диапазону [-1, 1] через сглаживание tanh(x / масштаб).
        Масштабы подобраны по типичной волатильности крипторынка:
        momentum_24h: масштаб 5%   -> tanh(m24/5)
        momentum_7d:  масштаб 10%  -> tanh(m7/10)
        volume_change: масштаб 20% -> tanh(vc/20)
        sentiment уже в [-1,1], используется как есть.

Шаг 2. Взвешенная сумма (веса подобраны так, чтобы новости и цена влияли примерно поровну,
        а объём был вспомогательным подтверждающим сигналом):
        weights = {momentum_24h: 0.35, momentum_7d: 0.15, volume_change: 0.15, sentiment: 0.35}
        raw_score = sum(weight_i * signal_i)   -> raw_score в [-1, 1]

Шаг 3. probability_up = (raw_score + 1) / 2, обрезается в [0.05, 0.95]
        (никогда не даём 100%/0% — рынок непредсказуем полностью).

Шаг 4. direction:
        probability_up > 0.55 -> UP
        probability_up < 0.45 -> DOWN
        иначе -> FLAT

Шаг 5. confidence (уверенность в самом прогнозе, отдельно от probability_up):
        - signal_agreement: доля сигналов, совпадающих по знаку с итоговым направлением
        - data_quality: штраф, если новостей мало (<3 за 48ч) или истории цены не хватает
        confidence = clip(0.3 + 0.5*signal_agreement + 0.2*data_quality_bonus, 0.3, 0.95)

Шаг 6. risk_level и risk_reasons считаются из волатильности и объёма новостей:
        HIGH  если volatility_7d > 4% (очень нестабильный актив) или новостей < 2
        MEDIUM если volatility_7d > 2% или сигналы противоречат друг другу
        LOW   иначе
"""
import math

from predictor.models import NormalizedMetric, Prediction

SCALES = {
    NormalizedMetric.MOMENTUM_24H: 5.0,
    NormalizedMetric.MOMENTUM_7D: 10.0,
    NormalizedMetric.VOLUME_CHANGE_24H: 20.0,
}

WEIGHTS = {
    NormalizedMetric.MOMENTUM_24H: 0.35,
    NormalizedMetric.MOMENTUM_7D: 0.15,
    NormalizedMetric.VOLUME_CHANGE_24H: 0.15,
    NormalizedMetric.NEWS_SENTIMENT_AVG: 0.35,
}

METRIC_LABELS = {
    NormalizedMetric.MOMENTUM_24H: "Momentum цены (24ч)",
    NormalizedMetric.MOMENTUM_7D: "Momentum цены (7д)",
    NormalizedMetric.VOLUME_CHANGE_24H: "Изменение объёма торгов (24ч)",
    NormalizedMetric.NEWS_SENTIMENT_AVG: "Тональность новостей (48ч)",
}


def _latest_metric(asset, metric_type):
    m = asset.metrics.filter(metric_type=metric_type).order_by("-computed_at").first()
    return m.value if m else None


def compute_prediction_for_asset(asset):
    """Считает Prediction для одного актива из его последних NormalizedMetric.
    Возвращает kwargs для Prediction.objects.create(...) либо None, если данных мало."""
    m24 = _latest_metric(asset, NormalizedMetric.MOMENTUM_24H)
    m7 = _latest_metric(asset, NormalizedMetric.MOMENTUM_7D)
    vc = _latest_metric(asset, NormalizedMetric.VOLUME_CHANGE_24H)
    vol7 = _latest_metric(asset, NormalizedMetric.VOLATILITY_7D)
    sent = _latest_metric(asset, NormalizedMetric.NEWS_SENTIMENT_AVG)
    nvol = _latest_metric(asset, NormalizedMetric.NEWS_VOLUME_48H)

    latest_price_rec = asset.price_records.order_by("-fetched_at").first()
    if latest_price_rec is None or m24 is None:
        return None  # недостаточно данных для прогноза

    sent = sent or 0.0
    nvol = nvol or 0
    vc = vc if vc is not None else 0.0
    m7 = m7 if m7 is not None else 0.0
    vol7 = vol7 if vol7 is not None else 0.0

    signals = {
        NormalizedMetric.MOMENTUM_24H: math.tanh(m24 / SCALES[NormalizedMetric.MOMENTUM_24H]),
        NormalizedMetric.MOMENTUM_7D: math.tanh(m7 / SCALES[NormalizedMetric.MOMENTUM_7D]),
        NormalizedMetric.VOLUME_CHANGE_24H: math.tanh(vc / SCALES[NormalizedMetric.VOLUME_CHANGE_24H]),
        NormalizedMetric.NEWS_SENTIMENT_AVG: sent,
    }

    raw_score = sum(WEIGHTS[k] * v for k, v in signals.items())
    probability_up = min(max((raw_score + 1) / 2, 0.05), 0.95)

    if probability_up > 0.55:
        direction = Prediction.UP
    elif probability_up < 0.45:
        direction = Prediction.DOWN
    else:
        direction = Prediction.FLAT

    # --- confidence ---
    target_sign = 1 if direction == Prediction.UP else (-1 if direction == Prediction.DOWN else 0)
    if target_sign == 0:
        agreement = sum(1 for v in signals.values() if abs(v) < 0.15) / len(signals)
    else:
        agreement = sum(1 for v in signals.values() if v * target_sign > 0) / len(signals)

    data_quality_bonus = 1.0
    if nvol < 3:
        data_quality_bonus -= 0.4
    if m7 == 0.0:
        data_quality_bonus -= 0.3
    data_quality_bonus = max(data_quality_bonus, 0.0)

    confidence = min(max(0.3 + 0.5 * agreement + 0.2 * data_quality_bonus, 0.3), 0.95)

    # --- risk ---
    risk_reasons = []
    risk_level = Prediction.LOW
    if vol7 > 4.0:
        risk_level = Prediction.HIGH
        risk_reasons.append(f"высокая историческая волатильность за 7д ({vol7:.2f}%) — актив может резко развернуться")
    elif vol7 > 2.0:
        risk_level = Prediction.MEDIUM
        risk_reasons.append(f"умеренная волатильность за 7д ({vol7:.2f}%)")

    if nvol < 2:
        risk_level = Prediction.HIGH
        risk_reasons.append(f"мало новостей за 48ч ({int(nvol)}) — сигнал тональности ненадёжен")

    disagreeing = [METRIC_LABELS[k] for k, v in signals.items() if target_sign != 0 and v * target_sign < -0.1]
    if disagreeing:
        if risk_level == Prediction.LOW:
            risk_level = Prediction.MEDIUM
        risk_reasons.append("сигналы противоречат друг другу: " + ", ".join(disagreeing) + " указывают в другую сторону")

    if not risk_reasons:
        risk_reasons.append("явных факторов риска в данных не обнаружено, но крипторынок всегда подвержен внешним шокам (регуляторные новости, форс-мажоры)")

    # --- человекочитаемые аргументы ---
    arg_lines = []
    for k, v in signals.items():
        raw_value = {
            NormalizedMetric.MOMENTUM_24H: m24,
            NormalizedMetric.MOMENTUM_7D: m7,
            NormalizedMetric.VOLUME_CHANGE_24H: vc,
            NormalizedMetric.NEWS_SENTIMENT_AVG: sent,
        }[k]
        direction_word = "в пользу роста" if v > 0.05 else ("в пользу падения" if v < -0.05 else "нейтрально")
        arg_lines.append(
            f"{METRIC_LABELS[k]}: сырое значение {raw_value:.2f}, вклад в скор {WEIGHTS[k]*v:+.3f} ({direction_word}, вес {WEIGHTS[k]})"
        )
    arguments = (
        f"Итоговый скор = {raw_score:+.3f} -> вероятность роста {probability_up*100:.1f}%. Разбивка по сигналам:\n- "
        + "\n- ".join(arg_lines)
        + f"\nНовостей за 48ч учтено: {int(nvol)}. Согласованность сигналов с направлением: {agreement*100:.0f}%."
    )

    breakdown = {
        "raw_inputs": {"momentum_24h": m24, "momentum_7d": m7, "volume_change_24h": vc,
                        "volatility_7d": vol7, "news_sentiment_avg": sent, "news_volume_48h": nvol},
        "normalized_signals": signals,
        "weights": WEIGHTS,
        "raw_score": raw_score,
        "probability_up": probability_up,
        "signal_agreement": agreement,
        "data_quality_bonus": data_quality_bonus,
    }

    return dict(
        asset=asset,
        horizon_hours=24,
        direction=direction,
        probability_up=probability_up,
        confidence=confidence,
        risk_level=risk_level,
        risk_reasons="; ".join(risk_reasons),
        arguments=arguments,
        score_breakdown=breakdown,
        price_at_prediction=latest_price_rec.price_usd,
    )


def generate_predictions(assets=None):
    """Считает прогнозы. assets=None -> для всех активных активов, либо для
    переданного списка/queryset (ОБЯЗАТЕЛЬНО объекты Asset, не Prediction!).

    Перед созданием новых прогнозов автоматически проверяет старые прогнозы,
    у которых истёк горизонт (24ч) — это и есть автоматическая сверка
    результатов: не нужно отдельно запускать evaluate_predictions вручную
    каждый раз, она подтягивается сама при каждом новом расчёте.
    """
    from predictor.models import Asset, Prediction
    from predictor.services.evaluation import evaluate_due_predictions

    try:
        evaluate_due_predictions()
    except Exception:
        pass  # автосверка не должна ронять создание новых прогнозов

    if assets is None:
        assets = Asset.objects.filter(is_active=True)

    created = []
    for asset in assets:
        kwargs = compute_prediction_for_asset(asset)
        if kwargs:
            created.append(Prediction.objects.create(**kwargs))
    return created