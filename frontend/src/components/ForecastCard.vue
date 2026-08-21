<template>
  <div v-if="loading" class="card">Загрузка...</div>

  <div v-else-if="!prediction && !loading" class="empty-state">
    Для {{ asset?.symbol }} ещё нет прогноза. Запустите на бэкенде
    <code>python manage.py run_predictions</code>.
  </div>

  <template v-else>
    <div class="card">
      <h2>{{ asset.symbol }} — прогноз на {{ prediction.horizon_hours }}ч</h2>
      <div class="sub">
        Создан {{ formatDate(prediction.created_at) }} по цене ${{
          prediction.price_at_prediction.toLocaleString()
        }}
        &nbsp;|&nbsp; статус:
        {{
          prediction.status === "EVALUATED" ? "проверен" : "ожидает проверки"
        }}
      </div>

      <div class="metrics-row">
        <div class="metric-box">
          <div class="label">Направление</div>
          <div class="value">
            <span class="badge" :class="prediction.direction">{{
              directionLabel(prediction.direction)
            }}</span>
          </div>
        </div>
        <div class="metric-box">
          <div class="label">Вероятность роста</div>
          <div class="value">
            {{ (prediction.probability_up * 100).toFixed(1) }}%
          </div>
        </div>
        <div class="metric-box">
          <div class="label">Уверенность модели</div>
          <div class="value">
            {{ (prediction.confidence * 100).toFixed(0) }}%
          </div>
        </div>
        <div class="metric-box">
          <div class="label">Уровень риска</div>
          <div class="value">
            <span class="badge" :class="prediction.risk_level">{{
              riskLabel(prediction.risk_level)
            }}</span>
          </div>
        </div>
      </div>

      <div class="section-title">
        Почему такой прогноз (аргументы алгоритма)
      </div>
      <div class="arguments">{{ prediction.arguments }}</div>

      <div class="section-title">Риски / почему прогноз может не сбыться</div>
      <div class="risk-box">{{ prediction.risk_reasons }}</div>

      <div v-if="prediction.outcome" class="section-title">
        Проверка прогноза (факт)
      </div>
      <div
        v-if="prediction.outcome"
        class="risk-box"
        :style="{
          color: prediction.outcome.was_correct ? '#86efac' : '#f8b4b4',
          background: prediction.outcome.was_correct ? '#132a1e' : '#221417',
          borderColor: prediction.outcome.was_correct ? '#1f4d33' : '#3a1620',
        }"
      >
        Факт: {{ directionLabel(prediction.outcome.actual_direction) }} ({{
          prediction.outcome.actual_pct_change.toFixed(2)
        }}%) —
        {{
          prediction.outcome.was_correct
            ? "✅ прогноз совпал с фактом"
            : "❌ прогноз не совпал с фактом"
        }}. Ошибка вероятности (компонент Brier):
        {{ prediction.outcome.probability_error.toFixed(3) }}
      </div>
    </div>

    <div class="card">
      <div class="section-title">Использованные нормализованные показатели</div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Показатель</th>
            <th>Значение</th>
            <th>Окно</th>
            <th>Рассчитан</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in metrics" :key="m.id">
            <td>{{ m.metric_type_label }}</td>
            <td>
              {{
                m.metric_type === "NEWS_VOLUME_48H"
                  ? Math.round(m.value)
                  : m.value.toFixed(3)
              }}
            </td>
            <td>{{ m.window_label }}</td>
            <td>{{ formatDate(m.computed_at) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!metrics.length" class="no-data">нет сохранённых метрик</div>
    </div>

    <div class="card">
      <div class="section-title">
        Новости, повлиявшие на тональность (сырые записи)
      </div>
      <div v-for="n in news" :key="n.id" class="news-item">
        <a :href="n.url" target="_blank" rel="noopener">{{ n.title }}</a>
        <span class="sent" :class="sentClass(n.sentiment_score)">
          [{{ n.sentiment_score.toFixed(2) }}]</span
        >
        <div class="meta">
          {{ n.source_name }} · {{ formatDate(n.published_at) }}
        </div>
      </div>
      <div v-if="!news.length" class="no-data">
        новостей по активу пока не собрано
      </div>
    </div>

    <div class="card">
      <div class="section-title">История прогнозов по {{ asset.symbol }}</div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Дата</th>
            <th>Направление</th>
            <th>P(рост)</th>
            <th>Уверенность</th>
            <th>Риск</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in history" :key="p.id">
            <td>{{ formatDate(p.created_at) }}</td>
            <td>
              <span class="badge" :class="p.direction">{{
                directionLabel(p.direction)
              }}</span>
            </td>
            <td>{{ (p.probability_up * 100).toFixed(0) }}%</td>
            <td>{{ (p.confidence * 100).toFixed(0) }}%</td>
            <td>
              <span class="badge" :class="p.risk_level">{{
                riskLabel(p.risk_level)
              }}</span>
            </td>
            <td>
              {{
                p.status === "EVALUATED"
                  ? p.outcome?.was_correct
                    ? "✅"
                    : "❌"
                  : "⏳"
              }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</template>

<script setup>
import { ref, watch, onMounted } from "vue";
import api from "../api.js";

const props = defineProps({ assetId: [Number, String], asset: Object });

const loading = ref(false);
const prediction = ref(null);
const metrics = ref([]);
const news = ref([]);
const history = ref([]);

async function load() {
  if (!props.assetId) return;
  loading.value = true;
  try {
    const [preds, m, n] = await Promise.all([
      api.getPredictions(props.assetId),
      api.getMetrics(props.assetId),
      api.getNews(props.assetId),
    ]);
    history.value = preds;
    prediction.value = preds[0] || null;
    metrics.value = m.slice(0, 6);
    news.value = n.slice(0, 8);
  } finally {
    loading.value = false;
  }
}

function directionLabel(d) {
  return { UP: "↑ Рост", DOWN: "↓ Падение", FLAT: "→ Флэт" }[d] || d;
}
function riskLabel(r) {
  return { LOW: "Низкий", MEDIUM: "Средний", HIGH: "Высокий" }[r] || r;
}
function sentClass(v) {
  return v > 0.1 ? "pos" : v < -0.1 ? "neg" : "neu";
}
function formatDate(d) {
  return new Date(d).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

onMounted(load);
watch(() => props.assetId, load);
</script>
