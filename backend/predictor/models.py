from django.db import models


class Source(models.Model):
    """Реальный источник данных: API цен или RSS-лента новостей."""

    PRICE_API = "PRICE_API"
    NEWS_RSS = "NEWS_RSS"
    TYPE_CHOICES = [(PRICE_API, "API цен"), (NEWS_RSS, "RSS-лента новостей")]

    name = models.CharField("Название", max_length=100, unique=True)
    source_type = models.CharField("Тип источника", max_length=20, choices=TYPE_CHOICES)
    url = models.URLField("Адрес (URL)")
    description = models.CharField("Описание", max_length=255, blank=True)

    class Meta:
        verbose_name = "Источник данных"
        verbose_name_plural = "Источники данных"

    def __str__(self):
        return self.name


class Asset(models.Model):
    """Объект прогноза — криптоактив (токен)."""

    symbol = models.CharField("Тикер", max_length=20, unique=True)
    name = models.CharField("Название", max_length=100)
    coingecko_id = models.CharField("ID на CoinGecko", max_length=100, unique=True)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Криптовалюта (токен)"
        verbose_name_plural = "Криптовалюты (токены)"

    def __str__(self):
        return self.symbol


class RawPriceRecord(models.Model):
    """Сырая запись цены/объёма из Price API на момент опроса."""

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="price_records", verbose_name="Источник")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="price_records", verbose_name="Актив")
    fetched_at = models.DateTimeField("Дата получения", auto_now_add=True)
    price_usd = models.FloatField("Цена, $")
    volume_24h_usd = models.FloatField("Объём торгов за 24ч, $", null=True, blank=True)
    market_cap_usd = models.FloatField("Капитализация, $", null=True, blank=True)
    pct_change_24h = models.FloatField("Изменение цены за 24ч, %", null=True, blank=True)
    raw_json = models.JSONField("Сырой ответ API", default=dict, blank=True)

    class Meta:
        ordering = ["-fetched_at"]
        verbose_name = "Запись цены"
        verbose_name_plural = "Записи цен"

    def __str__(self):
        return f"{self.asset.symbol} ${self.price_usd:.2f} @ {self.fetched_at:%Y-%m-%d %H:%M}"


class NewsArticle(models.Model):
    """Сырая новость из RSS-ленты."""

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="articles", verbose_name="Источник")
    title = models.CharField("Заголовок", max_length=500)
    url = models.URLField("Ссылка", unique=True)
    summary = models.TextField("Описание", blank=True)
    published_at = models.DateTimeField("Дата публикации")
    fetched_at = models.DateTimeField("Дата получения", auto_now_add=True)
    sentiment_score = models.FloatField("Тональность (-1..+1)", default=0.0)
    mentioned_assets = models.ManyToManyField(Asset, related_name="articles", blank=True, verbose_name="Упомянутые активы")

    class Meta:
        ordering = ["-published_at"]
        verbose_name = "Новость"
        verbose_name_plural = "Новости"

    def __str__(self):
        return self.title[:80]


class NormalizedMetric(models.Model):
    """Нормализованный показатель, посчитанный из сырых записей для одного актива."""

    MOMENTUM_24H = "MOMENTUM_24H"
    MOMENTUM_7D = "MOMENTUM_7D"
    VOLUME_CHANGE_24H = "VOLUME_CHANGE_24H"
    VOLATILITY_7D = "VOLATILITY_7D"
    NEWS_SENTIMENT_AVG = "NEWS_SENTIMENT_AVG"
    NEWS_VOLUME_48H = "NEWS_VOLUME_48H"

    # Метрики, которые по своей природе — целые числа (штуки), а не проценты/доли.
    INTEGER_METRICS = {NEWS_VOLUME_48H}

    METRIC_CHOICES = [
        (MOMENTUM_24H, "Ценовой моментум за 24ч, %"),
        (MOMENTUM_7D, "Ценовой моментум за 7д, %"),
        (VOLUME_CHANGE_24H, "Изменение объёма торгов за 24ч, %"),
        (VOLATILITY_7D, "Волатильность за 7д (std дневных доходностей)"),
        (NEWS_SENTIMENT_AVG, "Средняя тональность новостей (48ч, взвеш. по свежести)"),
        (NEWS_VOLUME_48H, "Количество новостей за 48ч, шт."),
    ]

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="metrics", verbose_name="Актив")
    metric_type = models.CharField("Тип показателя", max_length=30, choices=METRIC_CHOICES)
    value = models.FloatField("Значение")
    computed_at = models.DateTimeField("Дата расчёта", auto_now_add=True)
    window_label = models.CharField("Окно расчёта", max_length=80, blank=True)

    class Meta:
        ordering = ["-computed_at"]
        verbose_name = "Метрика"
        verbose_name_plural = "Метрики"

    def __str__(self):
        return f"{self.asset.symbol}:{self.metric_type}={self.value:.3f}"

    @property
    def value_display(self):
        """Целое число штук для 'количество новостей', иначе 3 знака после запятой."""
        if self.metric_type in self.INTEGER_METRICS:
            return str(int(round(self.value)))
        return f"{self.value:.3f}"


class Prediction(models.Model):
    """Итоговый прогноз, посчитанный алгоритмом из NormalizedMetric."""

    UP, DOWN, FLAT = "UP", "DOWN", "FLAT"
    DIRECTION_CHOICES = [(UP, "Рост"), (DOWN, "Падение"), (FLAT, "Без изменений")]

    LOW, MEDIUM, HIGH = "LOW", "MEDIUM", "HIGH"
    RISK_CHOICES = [(LOW, "Низкий"), (MEDIUM, "Средний"), (HIGH, "Высокий")]

    PENDING, EVALUATED = "PENDING", "EVALUATED"
    STATUS_CHOICES = [(PENDING, "Ожидает проверки"), (EVALUATED, "Проверен")]

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="predictions", verbose_name="Актив")
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    horizon_hours = models.IntegerField("Горизонт прогноза, ч", default=24)
    direction = models.CharField("Направление", max_length=10, choices=DIRECTION_CHOICES)
    probability_up = models.FloatField("Вероятность роста")
    confidence = models.FloatField("Уверенность модели")
    risk_level = models.CharField("Уровень риска", max_length=10, choices=RISK_CHOICES)
    risk_reasons = models.TextField("Причины риска")
    arguments = models.TextField("Аргументы алгоритма")
    score_breakdown = models.JSONField("Разбивка скора", default=dict)
    price_at_prediction = models.FloatField("Цена на момент прогноза, $")
    status = models.CharField("Статус", max_length=10, choices=STATUS_CHOICES, default=PENDING)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Прогноз"
        verbose_name_plural = "Прогнозы"

    def __str__(self):
        return f"{self.asset.symbol} {self.direction} p={self.probability_up:.2f} @ {self.created_at:%Y-%m-%d %H:%M}"


class PredictionOutcome(models.Model):
    """Проверка прогноза постфактум — как система понимает, что ошиблась."""

    prediction = models.OneToOneField(Prediction, on_delete=models.CASCADE, related_name="outcome", verbose_name="Прогноз")
    evaluated_at = models.DateTimeField("Дата проверки", auto_now_add=True)
    price_at_evaluation = models.FloatField("Цена на момент проверки, $")
    actual_pct_change = models.FloatField("Фактическое изменение, %")
    actual_direction = models.CharField("Фактическое направление", max_length=10, choices=Prediction.DIRECTION_CHOICES)
    was_correct = models.BooleanField("Прогноз сбылся")
    probability_error = models.FloatField("Ошибка вероятности (компонент Brier)")

    class Meta:
        verbose_name = "Результат прогноза"
        verbose_name_plural = "Результаты прогнозов"

    def __str__(self):
        return f"{self.prediction} -> {'OK' if self.was_correct else 'MISS'}"


class DataFetchLog(models.Model):
    """История обновлений данных — каждый запуск сбора данных."""

    SUCCESS, PARTIAL, FAILED = "SUCCESS", "PARTIAL", "FAILED"
    STATUS_CHOICES = [(SUCCESS, "Успех"), (PARTIAL, "Частично"), (FAILED, "Ошибка")]

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="fetch_logs", verbose_name="Источник")
    run_at = models.DateTimeField("Дата запуска", auto_now_add=True)
    status = models.CharField("Статус", max_length=10, choices=STATUS_CHOICES)
    records_fetched = models.IntegerField("Получено записей", default=0)
    error_message = models.TextField("Сообщение об ошибке", blank=True)

    class Meta:
        ordering = ["-run_at"]
        verbose_name = "Лог получения данных"
        verbose_name_plural = "Логи получения данных"

    def __str__(self):
        return f"{self.source.name} @ {self.run_at:%Y-%m-%d %H:%M} [{self.status}]"