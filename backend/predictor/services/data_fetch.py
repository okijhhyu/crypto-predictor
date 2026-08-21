"""
Слой сбора сырых данных из двух реальных источников:
1) CoinGecko API — цены, объём, капитализация, история за 8 дней.
2) RSS-ленты крипто-СМИ — заголовки/описания новостей.
Никаких платных ключей не требуется.

Есть два режима работы у каждой функции сбора:
- для ОДНОГО актива (fetch_price_for_asset) — используется кнопками "по этой монете"
  в админке и при автоматическом сборе данных для только что добавленного токена;
- для ВСЕХ активов (fetch_prices) — используется кнопкой "по всем монетам" и
  management-командой fetch_data.
В обоих случаях сразу же считаются производные метрики (моментум, волатильность,
тональность новостей) — отдельно их дозапускать не нужно.
"""
import datetime as dt
import statistics
import time

import requests
import feedparser
from dateutil import parser as dtparser
from django.utils import timezone
from django.conf import settings

from predictor.models import (
    Source, Asset, RawPriceRecord, NewsArticle, NormalizedMetric, DataFetchLog,
)
from predictor.services.sentiment import score_text

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


def get_or_create_price_source():
    price_source, _ = Source.objects.get_or_create(
        name="CoinGecko",
        defaults=dict(
            source_type=Source.PRICE_API,
            url=COINGECKO_BASE,
            description="Публичный бесплатный API цен/объёмов/капитализации криптоактивов",
        ),
    )
    return price_source


def get_or_create_default_news_sources():
    """Создаёт (если их ещё нет) стандартные RSS-источники из настроек.
    Дополнительные источники, добавленные вручную через админку, эта функция
    не трогает — они уже есть в базе и подхватываются get_all_news_sources()."""
    created = []
    for feed in settings.PREDICTOR_NEWS_FEEDS:
        s, _ = Source.objects.get_or_create(
            name=feed["name"],
            defaults=dict(source_type=Source.NEWS_RSS, url=feed["url"], description="RSS-лента крипто-новостей"),
        )
        created.append(s)
    return created


def get_all_news_sources():
    """ВСЕ RSS-источники из базы: и стандартные из настроек, и добавленные
    вручную через админку (Source с source_type=NEWS_RSS)."""
    get_or_create_default_news_sources()  # гарантируем, что дефолтные тоже есть в базе
    return list(Source.objects.filter(source_type=Source.NEWS_RSS))


def get_or_create_assets():
    """Гарантирует наличие активов по умолчанию из настроек и возвращает ВСЕ
    активные активы (включая добавленные вручную через админку)."""
    for a in settings.PREDICTOR_ASSETS:
        Asset.objects.get_or_create(
            coingecko_id=a["coingecko_id"],
            defaults=dict(symbol=a["symbol"], name=a["name"]),
        )
    return list(Asset.objects.filter(is_active=True))


def _get_with_retry(url, params, max_retries=3, base_timeout=20):
    """GET с ретраем на 429 (rate limit): ждём Retry-After (или растущую паузу) и повторяем."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=base_timeout)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 15 * (attempt + 1)))
                time.sleep(wait)
                last_exc = requests.HTTPError(f"429 Too Many Requests for url: {resp.url}")
                continue
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            last_exc = e
            if getattr(e.response, "status_code", None) != 429:
                raise
        except requests.RequestException as e:
            last_exc = e
            break
    raise last_exc


# ----------------------------------------------------------------------------
# Цены: вариант на ОДИН актив и вариант на ВСЕ активы
# ----------------------------------------------------------------------------

def fetch_price_for_asset(asset: Asset):
    """Полный сбор цены для ОДНОГО актива: текущая цена/объём/капа + 8-дневная
    история + сразу же расчёт momentum_24h, momentum_7d, volume_change_24h и
    волатильности. Используется кнопкой 'Собрать цены' по одной монете в админке
    и автоматически при добавлении нового токена.
    Возвращает (records_count, error_message)."""
    price_source = get_or_create_price_source()
    records_count = 0
    errors = []

    try:
        resp = _get_with_retry(
            f"{COINGECKO_BASE}/simple/price",
            params={
                "ids": asset.coingecko_id,
                "vs_currencies": "usd",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )
        d = resp.json().get(asset.coingecko_id)
        if d:
            RawPriceRecord.objects.create(
                source=price_source,
                asset=asset,
                price_usd=d.get("usd", 0.0),
                volume_24h_usd=d.get("usd_24h_vol"),
                market_cap_usd=d.get("usd_market_cap"),
                pct_change_24h=d.get("usd_24h_change"),
                raw_json=d,
            )
            records_count = 1
    except Exception as e:
        errors.append(f"simple/price: {e}")

    try:
        _fetch_history_and_store_metrics(asset, price_source)
    except Exception as e:
        errors.append(f"market_chart: {e}")

    # Метрики за 24ч (momentum_24h, volume_change_24h) считаются сразу же —
    # не нужно отдельно жать кнопку "рассчитать метрики".
    compute_price_momentum_metrics(assets=[asset])

    status = DataFetchLog.SUCCESS if not errors else (DataFetchLog.PARTIAL if records_count else DataFetchLog.FAILED)
    DataFetchLog.objects.create(
        source=price_source, status=status, records_fetched=records_count, error_message="; ".join(errors)
    )
    return records_count, "; ".join(errors)


def fetch_prices(assets=None):
    """Тянет цену/объём/капу + 24ч изменение для НЕСКОЛЬКИХ активов одним запросом
    (эффективнее, чем по одному), плюс 8-дневную историю по каждому активу отдельно.
    Метрики (моментум, волатильность) считаются сразу же по каждому активу.
    assets=None -> все активные активы (используется в management-командах и
    кнопке 'по всем монетам' в админке)."""
    price_source = get_or_create_price_source()
    if assets is None:
        assets = get_or_create_assets()
    assets = list(assets)
    if not assets:
        return 0, "Нет активов для сбора данных"

    ids = ",".join(a.coingecko_id for a in assets)
    records_count = 0
    errors = []

    try:
        resp = _get_with_retry(
            f"{COINGECKO_BASE}/simple/price",
            params={
                "ids": ids,
                "vs_currencies": "usd",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )
        data = resp.json()

        for asset in assets:
            d = data.get(asset.coingecko_id)
            if not d:
                continue
            RawPriceRecord.objects.create(
                source=price_source,
                asset=asset,
                price_usd=d.get("usd", 0.0),
                volume_24h_usd=d.get("usd_24h_vol"),
                market_cap_usd=d.get("usd_market_cap"),
                pct_change_24h=d.get("usd_24h_change"),
                raw_json=d,
            )
            records_count += 1

    except Exception as e:  # сеть недоступна, лимит запросов и т.д.
        errors.append(f"simple/price: {e}")

    # История за 8 дней (momentum_7d, волатильность) — по каждому активу ОТДЕЛЬНО.
    # Ошибка по одной монете (например 429) не должна ронять остальные.
    for i, asset in enumerate(assets):
        if i > 0:
            time.sleep(2.0)  # уважаем rate limit публичного API
        try:
            _fetch_history_and_store_metrics(asset, price_source)
        except Exception as e:
            errors.append(f"market_chart[{asset.symbol}]: {e}")

    # Метрики за 24ч считаются сразу же для всех обработанных активов — весь
    # цикл "получить цены -> посчитать показатели" укладывается в один вызов.
    compute_price_momentum_metrics(assets=assets)

    status = DataFetchLog.SUCCESS if not errors else (DataFetchLog.PARTIAL if records_count else DataFetchLog.FAILED)
    error = "; ".join(errors)
    DataFetchLog.objects.create(
        source=price_source, status=status, records_fetched=records_count, error_message=error
    )
    return records_count, error


def _fetch_history_and_store_metrics(asset: Asset, price_source: Source):
    """Забирает 8-дневный график цены, считает momentum_7d и волатильность,
    сохраняет как NormalizedMetric. momentum_24h считается отдельно, см.
    compute_price_momentum_metrics()."""
    resp = _get_with_retry(
        f"{COINGECKO_BASE}/coins/{asset.coingecko_id}/market_chart",
        params={"vs_currency": "usd", "days": 8, "interval": "daily"},
    )
    prices = [p[1] for p in resp.json().get("prices", [])]
    if len(prices) < 3:
        return

    momentum_7d = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] else 0.0

    returns = [
        (prices[i] - prices[i - 1]) / prices[i - 1]
        for i in range(1, len(prices))
        if prices[i - 1]
    ]
    volatility_7d = statistics.pstdev(returns) * 100 if len(returns) > 1 else 0.0

    NormalizedMetric.objects.create(
        asset=asset, metric_type=NormalizedMetric.MOMENTUM_7D, value=momentum_7d,
        window_label="8 дневных точек (CoinGecko market_chart)",
    )
    NormalizedMetric.objects.create(
        asset=asset, metric_type=NormalizedMetric.VOLATILITY_7D, value=volatility_7d,
        window_label="std дневных доходностей за 7д",
    )


def compute_price_momentum_metrics(assets=None):
    """momentum_24h и volume_change_24h считаются из двух последних RawPriceRecord.
    assets=None -> все активные активы."""
    if assets is None:
        assets = Asset.objects.filter(is_active=True)
    for asset in assets:
        recs = list(asset.price_records.order_by("-fetched_at")[:2])
        if len(recs) < 1:
            continue
        latest = recs[0]
        if latest.pct_change_24h is not None:
            NormalizedMetric.objects.create(
                asset=asset, metric_type=NormalizedMetric.MOMENTUM_24H, value=latest.pct_change_24h,
                window_label="из CoinGecko usd_24h_change",
            )
        if len(recs) == 2 and recs[1].volume_24h_usd:
            prev = recs[1]
            change = (latest.volume_24h_usd - prev.volume_24h_usd) / prev.volume_24h_usd * 100
            NormalizedMetric.objects.create(
                asset=asset, metric_type=NormalizedMetric.VOLUME_CHANGE_24H, value=change,
                window_label="между двумя последними опросами API",
            )


# ----------------------------------------------------------------------------
# Новости: RSS не привязан к конкретной монете, поэтому сбор всегда идёт по
# ВСЕМ лентам сразу, но пересчёт тональности (compute_news_metrics) можно
# ограничить нужными активами.
# ----------------------------------------------------------------------------

def fetch_news():
    """Парсит ВСЕ RSS-ленты, которые есть в базе (и стандартные из настроек,
    и добавленные вручную через админку), сохраняет новые статьи, считает
    тональность и привязывает статью к активам, упомянутым в тексте.
    Возвращает общее количество новых статей."""
    news_sources = get_all_news_sources()
    assets = get_or_create_assets()

    total_new = 0
    for source in news_sources:
        status = DataFetchLog.SUCCESS
        error = ""
        new_count = 0
        try:
            feed = feedparser.parse(source.url)
            if getattr(feed, "bozo", False) and not feed.entries:
                raise RuntimeError(str(feed.bozo_exception))

            for entry in feed.entries[:40]:
                url = entry.get("link", "")
                if not url or NewsArticle.objects.filter(url=url).exists():
                    continue
                title = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                try:
                    published = dtparser.parse(entry.get("published", "")) if entry.get("published") else timezone.now()
                    if timezone.is_naive(published):
                        published = timezone.make_aware(published, dt.timezone.utc)
                except Exception:
                    published = timezone.now()

                full_text = f"{title} {summary}"
                sentiment = score_text(full_text)

                article = NewsArticle.objects.create(
                    source=source, title=title, url=url, summary=summary[:2000],
                    published_at=published, sentiment_score=sentiment,
                )
                text_lower = full_text.lower()
                for asset in assets:
                    if asset.name.lower() in text_lower or f" {asset.symbol.lower()} " in f" {text_lower} ":
                        article.mentioned_assets.add(asset)
                new_count += 1

        except Exception as e:
            status = DataFetchLog.FAILED if new_count == 0 else DataFetchLog.PARTIAL
            error = str(e)

        DataFetchLog.objects.create(source=source, status=status, records_fetched=new_count, error_message=error)
        total_new += new_count

    # Тональность/кол-во новостей пересчитываем сразу же по всем активам —
    # отдельно жать "рассчитать метрики" после сбора новостей не нужно.
    compute_news_metrics()
    return total_new


def compute_news_metrics(assets=None):
    """После сбора новостей считает по каждому активу:
    - news_sentiment_avg: средняя тональность за 48ч, взвешенная по свежести (экспон. затухание)
    - news_volume_48h: сколько статей вообще упомянули актив за 48ч (ЦЕЛОЕ число)
    assets=None -> все активные активы."""
    if assets is None:
        assets = Asset.objects.filter(is_active=True)
    now = timezone.now()
    window_start = now - dt.timedelta(hours=48)
    for asset in assets:
        articles = asset.articles.filter(published_at__gte=window_start)
        n = articles.count()  # уже целое число (Count SQL) — сохраняем как int
        if n == 0:
            NormalizedMetric.objects.create(
                asset=asset, metric_type=NormalizedMetric.NEWS_SENTIMENT_AVG, value=0.0,
                window_label="48ч, статей не найдено",
            )
            NormalizedMetric.objects.create(
                asset=asset, metric_type=NormalizedMetric.NEWS_VOLUME_48H, value=0,
                window_label="48ч",
            )
            continue

        weighted_sum = 0.0
        weight_total = 0.0
        for art in articles:
            age_hours = max((now - art.published_at).total_seconds() / 3600.0, 0)
            weight = 0.5 ** (age_hours / 24.0)  # чем свежее, тем больше вес (полураспад 24ч)
            weighted_sum += art.sentiment_score * weight
            weight_total += weight
        avg_sentiment = weighted_sum / weight_total if weight_total else 0.0

        NormalizedMetric.objects.create(
            asset=asset, metric_type=NormalizedMetric.NEWS_SENTIMENT_AVG, value=avg_sentiment,
            window_label=f"48ч, {n} статей, экспон. вес по свежести",
        )
        NormalizedMetric.objects.create(
            asset=asset, metric_type=NormalizedMetric.NEWS_VOLUME_48H, value=int(n),
            window_label="48ч",
        )


def run_full_fetch(assets=None):
    """Полный цикл сбора для management-команды fetch_data: цены -> новости
    (метрики считаются внутри fetch_prices/fetch_news автоматически)."""
    price_count, price_err = fetch_prices(assets=assets)
    news_count = fetch_news()
    return {"price_records": price_count, "price_error": price_err, "news_records": news_count}