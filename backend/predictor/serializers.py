from rest_framework import serializers

from predictor.models import (
    Asset, Source, RawPriceRecord, NewsArticle, NormalizedMetric,
    Prediction, PredictionOutcome, DataFetchLog,
)


class SourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Source
        fields = ["id", "name", "source_type", "url", "description"]


class NewsArticleSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source="source.name", read_only=True)

    class Meta:
        model = NewsArticle
        fields = ["id", "source_name", "title", "url", "summary", "published_at", "sentiment_score"]


class NormalizedMetricSerializer(serializers.ModelSerializer):
    metric_type_label = serializers.CharField(source="get_metric_type_display", read_only=True)

    class Meta:
        model = NormalizedMetric
        fields = ["id", "metric_type", "metric_type_label", "value", "computed_at", "window_label"]


class PredictionOutcomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PredictionOutcome
        fields = ["evaluated_at", "price_at_evaluation", "actual_pct_change", "actual_direction",
                  "was_correct", "probability_error"]


class PredictionSerializer(serializers.ModelSerializer):
    outcome = PredictionOutcomeSerializer(read_only=True)
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)

    class Meta:
        model = Prediction
        fields = [
            "id", "asset", "asset_symbol", "created_at", "horizon_hours", "direction",
            "probability_up", "confidence", "risk_level", "risk_reasons", "arguments",
            "score_breakdown", "price_at_prediction", "status", "outcome",
        ]


class AssetSerializer(serializers.ModelSerializer):
    latest_prediction = serializers.SerializerMethodField()
    latest_price = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = ["id", "symbol", "name", "coingecko_id", "is_active", "latest_price", "latest_prediction"]

    def get_latest_price(self, obj):
        rec = obj.price_records.order_by("-fetched_at").first()
        if not rec:
            return None
        return {"price_usd": rec.price_usd, "pct_change_24h": rec.pct_change_24h, "fetched_at": rec.fetched_at}

    def get_latest_prediction(self, obj):
        pred = obj.predictions.order_by("-created_at").first()
        if not pred:
            return None
        return {
            "id": pred.id, "direction": pred.direction, "probability_up": pred.probability_up,
            "confidence": pred.confidence, "risk_level": pred.risk_level, "created_at": pred.created_at,
        }


class DataFetchLogSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source="source.name", read_only=True)

    class Meta:
        model = DataFetchLog
        fields = ["id", "source_name", "run_at", "status", "records_fetched", "error_message"]


class RawPriceRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = RawPriceRecord
        fields = ["id", "asset", "fetched_at", "price_usd", "volume_24h_usd", "market_cap_usd", "pct_change_24h"]
