from rest_framework import viewsets, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from predictor.models import Asset, Prediction, NewsArticle, NormalizedMetric, DataFetchLog
from predictor.serializers import (
    AssetSerializer, PredictionSerializer, NewsArticleSerializer,
    NormalizedMetricSerializer, DataFetchLogSerializer,
)
from predictor.services.evaluation import accuracy_summary, evaluate_due_predictions
from predictor.services.data_fetch import fetch_prices, fetch_price_for_asset, fetch_news, run_full_fetch
from predictor.services.scoring import generate_predictions


class AssetViewSet(viewsets.ReadOnlyModelViewSet):
    """Список активов (объектов прогноза) с последней ценой и последним прогнозом."""
    queryset = Asset.objects.filter(is_active=True)
    serializer_class = AssetSerializer


class PredictionViewSet(viewsets.ReadOnlyModelViewSet):
    """Карточки прогнозов. ?asset=<id> фильтрует по активу."""
    serializer_class = PredictionSerializer

    def get_queryset(self):
        qs = Prediction.objects.select_related("asset").all().order_by("-created_at")
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        return qs


class NewsArticleViewSet(viewsets.ReadOnlyModelViewSet):
    """Сырые новости, использованные в анализе. ?asset=<id> фильтрует по упоминанию."""
    serializer_class = NewsArticleSerializer

    def get_queryset(self):
        qs = NewsArticle.objects.all().order_by("-published_at")
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            # distinct() нужен: у статьи может быть несколько mentioned_assets,
            # без него join по M2M мог бы задублировать строки
            qs = qs.filter(mentioned_assets__id=asset_id).distinct()
        return qs[:200]


class MetricViewSet(viewsets.ReadOnlyModelViewSet):
    """Нормализованные показатели по активу. ?asset=<id>"""
    serializer_class = NormalizedMetricSerializer

    def get_queryset(self):
        qs = NormalizedMetric.objects.all().order_by("-computed_at")
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        return qs[:100]


class FetchLogViewSet(viewsets.ReadOnlyModelViewSet):
    """История обновлений данных (пункт 3 задания)."""
    queryset = DataFetchLog.objects.all().order_by("-run_at")[:100]
    serializer_class = DataFetchLogSerializer


@api_view(["GET"])
def model_health(request):
    """Сводка калибровки модели — 'как система понимает, что ошиблась' в цифрах."""
    return Response(accuracy_summary())


# ============================================================================
# API-действия — те же операции, что и кнопки в админке, но доступные по HTTP.
# Удобно для проверки/тестирования (Postman, curl, автотесты) без захода в
# админку. ВНИМАНИЕ: в проде такие эндпоинты нужно закрывать авторизацией —
# здесь они открыты специально для простоты проверки прототипа.
# ============================================================================

def _get_asset_or_none(request):
    asset_id = request.query_params.get("asset") or request.data.get("asset")
    if not asset_id:
        return None, None
    try:
        return Asset.objects.get(pk=asset_id), None
    except Asset.DoesNotExist:
        return None, Response({"error": f"Актив с id={asset_id} не найден"}, status=status.HTTP_404_NOT_FOUND)


@api_view(["POST"])
def action_fetch_prices(request):
    """POST /api/actions/fetch-prices/  — по всем активам
    POST /api/actions/fetch-prices/?asset=<id>  — по одному активу"""
    asset, err = _get_asset_or_none(request)
    if err:
        return err
    if asset:
        count, error = fetch_price_for_asset(asset)
    else:
        count, error = fetch_prices()
    return Response({"records_fetched": count, "error": error or None})


@api_view(["POST"])
def action_fetch_news(request):
    """POST /api/actions/fetch-news/ — новости не привязаны к монете,
    поэтому всегда собираются по всем RSS-лентам сразу."""
    count = fetch_news()
    return Response({"new_articles": count})


@api_view(["POST"])
def action_run_predictions(request):
    """POST /api/actions/run-predictions/  — по всем активам
    POST /api/actions/run-predictions/?asset=<id>  — по одному активу"""
    asset, err = _get_asset_or_none(request)
    if err:
        return err
    assets = [asset] if asset else None
    predictions = generate_predictions(assets=assets)
    return Response(PredictionSerializer(predictions, many=True).data)


@api_view(["POST"])
def action_evaluate_predictions(request):
    """POST /api/actions/evaluate/ — сверяет прогнозы с истёкшим горизонтом с фактом."""
    evaluated = evaluate_due_predictions()
    return Response({
        "evaluated_count": len(evaluated),
        "accuracy_summary": accuracy_summary(),
    })


@api_view(["POST"])
def action_run_cycle(request):
    """POST /api/actions/run-cycle/  — полный цикл: цены -> новости -> сверка -> прогноз.
    ?asset=<id> ограничивает цены и прогноз одним активом (новости всё равно
    собираются по всем лентам, т.к. RSS не привязан к монете)."""
    asset, err = _get_asset_or_none(request)
    if err:
        return err
    assets = [asset] if asset else None

    price_count, price_err = fetch_prices(assets=assets)
    news_count = fetch_news()
    evaluated = evaluate_due_predictions()
    predictions = generate_predictions(assets=assets)

    return Response({
        "price_records": price_count,
        "price_error": price_err or None,
        "news_records": news_count,
        "evaluated_count": len(evaluated),
        "predictions": PredictionSerializer(predictions, many=True).data,
    })