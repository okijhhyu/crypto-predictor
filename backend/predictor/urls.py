from django.urls import path, include
from rest_framework.routers import DefaultRouter

from predictor import views

router = DefaultRouter()
router.register("assets", views.AssetViewSet, basename="asset")
router.register("predictions", views.PredictionViewSet, basename="prediction")
router.register("news", views.NewsArticleViewSet, basename="news")
router.register("metrics", views.MetricViewSet, basename="metric")
router.register("fetch-logs", views.FetchLogViewSet, basename="fetchlog")

urlpatterns = [
    path("", include(router.urls)),
    path("model-health/", views.model_health, name="model-health"),
    # Действия для проверки/тестирования — см. README раздел "API для проверки"
    path("actions/fetch-prices/", views.action_fetch_prices, name="action-fetch-prices"),
    path("actions/fetch-news/", views.action_fetch_news, name="action-fetch-news"),
    path("actions/run-predictions/", views.action_run_predictions, name="action-run-predictions"),
    path("actions/evaluate/", views.action_evaluate_predictions, name="action-evaluate"),
    path("actions/run-cycle/", views.action_run_cycle, name="action-run-cycle"),
]