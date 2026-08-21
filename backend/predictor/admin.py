from django.contrib import admin, messages
from django.urls import path
from django.http import HttpResponseRedirect

from predictor.models import (
    Source,
    Asset,
    RawPriceRecord,
    NewsArticle,
    NormalizedMetric,
    Prediction,
    PredictionOutcome,
    DataFetchLog,
)

from predictor.services.data_fetch import (
    fetch_news,
    fetch_prices,
    fetch_price_for_asset,
    compute_price_momentum_metrics,
    compute_news_metrics,
)
from predictor.services.scoring import generate_predictions
from predictor.services.evaluation import evaluate_due_predictions, accuracy_summary


# ============================================================================
# Источники данных
# ============================================================================

@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "source_type", "url")
    list_filter = ("source_type",)
    search_fields = ("name", "url")
    fieldsets = (
        (None, {
            "fields": ("name", "source_type", "url", "description"),
            "description": (
                "Чтобы добавить свою RSS-ленту новостей — создайте здесь запись с типом "
                "«RSS-лента новостей» и укажите её адрес. Она автоматически подключится "
                "к следующему сбору новостей (кнопка «Собрать новости» ниже)."
            ),
        }),
    )


# ============================================================================
# Активы (токены) — действия ПО ОДНОЙ монете (для выбранных строк) и
# ПО ВСЕМ монетам (выбрать всё галочкой сверху таблицы — это тот же механизм)
# ============================================================================

@admin.action(description="① Собрать цены (для выбранных)")
def action_fetch_prices(modeladmin, request, queryset):
    total, errors = 0, []
    for asset in queryset:
        count, err = fetch_price_for_asset(asset)
        total += count
        if err:
            errors.append(f"{asset.symbol}: {err}")
    if errors:
        messages.warning(request, f"Цены собраны частично. Записей: {total}. Проблемы: {'; '.join(errors)}")
    else:
        messages.success(request, f"Цены собраны для {queryset.count()} актив(ов). Новых записей: {total}.")


@admin.action(description="② Собрать новости (по всем лентам)")
def action_fetch_news(modeladmin, request, queryset):
    # RSS-лента не привязана к конкретной монете — новости собираются всегда
    # по всем источникам сразу, но тональность/счётчики пересчитываются
    # именно для выбранных активов.
    try:
        count = fetch_news()
        compute_news_metrics(assets=list(queryset))
        messages.success(
            request,
            f"Новости собраны (по всем RSS-лентам, т.к. лента не привязана к одной монете). "
            f"Новых статей: {count}. Тональность пересчитана для: {', '.join(a.symbol for a in queryset)}.",
        )
    except Exception as exc:
        messages.error(request, f"Ошибка сбора новостей: {exc}")


@admin.action(description="③ Сделать прогноз (для выбранных)")
def action_generate_predictions(modeladmin, request, queryset):
    try:
        predictions = generate_predictions(assets=queryset)
        if predictions:
            messages.success(
                request,
                f"Создано прогнозов: {len(predictions)} — "
                + ", ".join(f"{p.asset.symbol}: {p.direction} ({p.probability_up*100:.0f}%)" for p in predictions),
            )
        else:
            messages.warning(request, "Прогноз не создан: недостаточно собранных данных (сначала соберите цены).")
    except Exception as exc:
        messages.error(request, f"Ошибка создания прогнозов: {exc}")


@admin.action(description="🔁 Полный цикл: цены + новости + прогноз (для выбранных)")
def action_full_cycle(modeladmin, request, queryset):
    action_fetch_prices(modeladmin, request, queryset)
    action_fetch_news(modeladmin, request, queryset)
    action_generate_predictions(modeladmin, request, queryset)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "coingecko_id", "is_active", "col_latest_price", "col_latest_prediction")
    list_filter = ("is_active",)
    search_fields = ("symbol", "name", "coingecko_id")
    actions = [action_fetch_prices, action_fetch_news, action_generate_predictions, action_full_cycle]
    fieldsets = (
        (None, {
            "fields": ("symbol", "name", "coingecko_id", "is_active"),
            "description": (
                "Чтобы добавить новый токен — узнайте его ID на CoinGecko "
                "(страница монеты на coingecko.com, часть адреса после /coins/). "
                "После сохранения данные по нему будут собраны автоматически."
            ),
        }),
    )

    @admin.display(description="Текущая цена")
    def col_latest_price(self, obj):
        rec = obj.price_records.order_by("-fetched_at").first()
        if not rec:
            return "—"
        arrow = "▲" if (rec.pct_change_24h or 0) >= 0 else "▼"
        return f"${rec.price_usd:,.2f} {arrow} {rec.pct_change_24h:.2f}%" if rec.pct_change_24h is not None else f"${rec.price_usd:,.2f}"

    @admin.display(description="Последний прогноз")
    def col_latest_prediction(self, obj):
        pred = obj.predictions.order_by("-created_at").first()
        if not pred:
            return "—"
        labels = {"UP": "Рост", "DOWN": "Падение", "FLAT": "Флэт"}
        return f"{labels.get(pred.direction, pred.direction)} ({pred.probability_up*100:.0f}%), риск {pred.risk_level}"

    def save_model(self, request, obj, form, change):
        """При добавлении НОВОГО токена сразу же запускаем для него сбор данных
        (цены + метрики). Новости — по общим лентам, поэтому не дублируем запрос
        сюда, они подтянутся по кнопке «Собрать новости» или следующим запуском
        run_cycle. Если CoinGecko недоступен/не знает такой id — просто
        показываем предупреждение, сам токен всё равно сохраняется."""
        is_new = not change
        super().save_model(request, obj, form, change)
        if is_new:
            try:
                count, err = fetch_price_for_asset(obj)
                if count:
                    messages.success(request, f"Токен «{obj.symbol}» добавлен, начальные данные по цене собраны.")
                else:
                    messages.warning(
                        request,
                        f"Токен «{obj.symbol}» добавлен, но собрать цену не удалось "
                        f"(проверьте правильность CoinGecko ID). Ошибка: {err}",
                    )
            except Exception as exc:
                messages.warning(request, f"Токен «{obj.symbol}» добавлен, но автосбор данных завершился с ошибкой: {exc}")


# ============================================================================
# Сырые записи цены — только просмотр + общая кнопка "собрать по всем"
# ============================================================================

@admin.register(RawPriceRecord)
class RawPriceRecordAdmin(admin.ModelAdmin):
    list_display = ("asset", "price_usd", "volume_24h_usd", "market_cap_usd", "pct_change_24h", "fetched_at")
    list_filter = ("asset", "source")
    change_list_template = "admin/predictor/rawpricerecord/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("fetch-prices/", self.admin_site.admin_view(self.fetch_prices_view), name="fetch-prices"),
        ]
        return custom_urls + urls

    def fetch_prices_view(self, request):
        """Собирает цены по ВСЕМ активным монетам сразу (метрики momentum/
        volatility считаются автоматически внутри fetch_prices)."""
        try:
            count, error = fetch_prices()
            if error:
                self.message_user(request, f"Цены обновлены частично. Получено записей: {count}. Ошибка: {error}", messages.WARNING)
            else:
                self.message_user(request, f"Цены успешно получены по всем монетам. Новых записей: {count}.", messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Ошибка получения цен: {exc}", messages.ERROR)
        return HttpResponseRedirect("../")


# ============================================================================
# Новости
# ============================================================================

@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "published_at", "sentiment_score", "col_assets")
    list_filter = ("source", "published_at")
    search_fields = ("title", "summary", "url")
    change_list_template = "admin/predictor/newsarticle/change_list.html"

    @admin.display(description="Упомянутые активы")
    def col_assets(self, obj):
        return ", ".join(a.symbol for a in obj.mentioned_assets.all()) or "—"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("fetch-news/", self.admin_site.admin_view(self.fetch_news_view), name="fetch-news"),
        ]
        return custom_urls + urls

    def fetch_news_view(self, request):
        """Собирает новости по ВСЕМ RSS-лентам в базе, включая добавленные
        вручную через Source. Тональность пересчитывается автоматически."""
        try:
            count = fetch_news()
            self.message_user(request, f"Новости получены по всем лентам. Новых новостей: {count}.", messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Ошибка получения новостей: {exc}", messages.ERROR)
        return HttpResponseRedirect("../")


# ============================================================================
# Нормализованные метрики
# ============================================================================

@admin.register(NormalizedMetric)
class NormalizedMetricAdmin(admin.ModelAdmin):
    list_display = ("asset", "metric_type", "col_value", "window_label", "computed_at")
    list_filter = ("metric_type", "asset")
    change_list_template = "admin/predictor/normalizedmetric/change_list.html"

    @admin.display(description="Значение")
    def col_value(self, obj):
        # Количество новостей показываем целым числом, остальное — с 3 знаками.
        return obj.value_display

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("calculate-metrics/", self.admin_site.admin_view(self.calculate_metrics_view), name="calculate-metrics"),
        ]
        return custom_urls + urls

    def calculate_metrics_view(self, request):
        """Пересчёт метрик вручную, на случай если нужно обновить их без
        повторного похода в CoinGecko/RSS (обычно не требуется — метрики
        считаются автоматически внутри кнопок сбора цен/новостей)."""
        try:
            compute_price_momentum_metrics()
            compute_news_metrics()
            self.message_user(request, "Метрики успешно пересчитаны из уже собранных данных.", messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Ошибка расчёта метрик: {exc}", messages.ERROR)
        return HttpResponseRedirect("../")


# ============================================================================
# Прогнозы
# ============================================================================

@admin.register(Prediction)
class PredictionAdmin(admin.ModelAdmin):
    list_display = ("asset", "direction", "probability_up", "confidence", "risk_level", "status", "created_at")
    list_filter = ("asset", "direction", "risk_level", "status", "created_at")
    change_list_template = "admin/predictor/prediction/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("create-predictions/", self.admin_site.admin_view(self.create_predictions_view), name="create-predictions"),
            path("evaluate-predictions/", self.admin_site.admin_view(self.evaluate_predictions_view), name="evaluate-predictions"),
        ]
        return custom_urls + urls

    def create_predictions_view(self, request):
        """Прогноз по ВСЕМ активным монетам (для одной монеты — кнопка на
        странице Криптовалюты)."""
        try:
            predictions = generate_predictions()  # None -> все активные активы
            self.message_user(request, f"Прогнозы успешно созданы. Новых прогнозов: {len(predictions)}.", messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Ошибка создания прогнозов: {exc}", messages.ERROR)
        return HttpResponseRedirect("../")

    def evaluate_predictions_view(self, request):
        """Ручной запуск сверки прогнозов с фактом (для тех, у кого истёк
        горизонт 24ч). Сверка также срабатывает автоматически при каждом
        новом расчёте прогноза — эта кнопка нужна, если хочется свежую
        статистику без создания новых прогнозов."""
        try:
            evaluated = evaluate_due_predictions()
            summary = accuracy_summary()
            self.message_user(
                request,
                f"Проверено прогнозов: {len(evaluated)}. Итого точность направления: "
                f"{summary['hit_rate']*100:.0f}% из {summary['total_evaluated']}" if summary["hit_rate"] is not None
                else f"Проверено прогнозов: {len(evaluated)}. Пока нет ни одного прогноза с истёкшим горизонтом.",
                messages.SUCCESS,
            )
        except Exception as exc:
            self.message_user(request, f"Ошибка проверки прогнозов: {exc}", messages.ERROR)
        return HttpResponseRedirect("../")


@admin.register(PredictionOutcome)
class PredictionOutcomeAdmin(admin.ModelAdmin):
    list_display = ("prediction", "actual_direction", "was_correct", "probability_error", "evaluated_at")
    list_filter = ("was_correct", "actual_direction")


@admin.register(DataFetchLog)
class DataFetchLogAdmin(admin.ModelAdmin):
    list_display = ("source", "status", "records_fetched", "run_at")
    list_filter = ("source", "status")