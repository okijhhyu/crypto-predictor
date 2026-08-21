<template>
  <div class="header">
    <h1>🔮 Предсказатель крипторынка</h1>
    <p>Прогноз направления цены на 24ч на основе моментума цены/объёма (CoinGecko) и тональности новостей (RSS)</p>
  </div>

  <div class="health-bar" v-if="health">
    <div>Проверено прогнозов: <b>{{ health.total_evaluated }}</b></div>
    <div v-if="health.hit_rate !== null">Точность направления: <b>{{ (health.hit_rate * 100).toFixed(0) }}%</b></div>
    <div v-if="health.avg_brier_component !== null">Средняя ошибка вероятности (Brier): <b>{{ health.avg_brier_component }}</b></div>
    <div v-if="health.total_evaluated === 0" class="no-data">Пока нет проверенных прогнозов — статистика появится через 24ч после первых прогнозов</div>
  </div>

  <div class="asset-grid">
    <AssetCard
      v-for="asset in assets"
      :key="asset.id"
      :asset="asset"
      :active="asset.id === selectedAssetId"
      @click="selectAsset(asset.id)"
    />
  </div>

  <div v-if="!assets.length" class="empty-state">
    Список активов пуст. На бэкенде выполните: <code>python manage.py fetch_data</code>, затем
    <code>python manage.py run_predictions</code>.
  </div>

  <ForecastCard
    v-if="selectedAssetId"
    :asset-id="selectedAssetId"
    :asset="selectedAsset"
  />

  <div class="disclaimer">
    ⚠️ Прототип создан в учебных целях. Это не финансовая рекомендация, реальные деньги/ставки не подключаются,
    доходность не гарантируется. Все прогнозы — результат прозрачного скоринга по историческим данным и не являются
    точным предсказанием будущего.
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import api from './api.js'
import AssetCard from './components/AssetCard.vue'
import ForecastCard from './components/ForecastCard.vue'

const assets = ref([])
const selectedAssetId = ref(null)
const health = ref(null)

const selectedAsset = computed(() => assets.value.find(a => a.id === selectedAssetId.value))

function selectAsset(id) {
  selectedAssetId.value = id
}

onMounted(async () => {
  try {
    assets.value = await api.getAssets()
    const withData = assets.value.find(a => a.latest_prediction)
    selectedAssetId.value = (withData ?? assets.value[0])?.id ?? null
  } catch (e) {
    console.error('Не удалось загрузить активы. Убедитесь, что backend запущен на :8000', e)
  }
  try {
    health.value = await api.getModelHealth()
  } catch (e) { /* backend недоступен */ }
})
</script>
