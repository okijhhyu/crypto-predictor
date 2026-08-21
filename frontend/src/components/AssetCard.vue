<template>
  <div class="asset-card" :class="{ active }" @click="$emit('click')">
    <div class="symbol">{{ asset.symbol }}</div>
    <div class="name">{{ asset.name }}</div>
    <div class="price" v-if="asset.latest_price">
      ${{ formatPrice(asset.latest_price.price_usd) }}
      <span :style="{ color: asset.latest_price.pct_change_24h >= 0 ? '#4ade80' : '#f87171' }">
        ({{ asset.latest_price.pct_change_24h?.toFixed(2) }}%)
      </span>
    </div>
    <div class="no-data" v-else>нет свежих данных</div>

    <div v-if="asset.latest_prediction">
      <span class="badge" :class="asset.latest_prediction.direction">
        {{ directionLabel(asset.latest_prediction.direction) }}
      </span>
      <span style="margin-left:6px; font-size:12px; color:#9aa0ac;">
        {{ (asset.latest_prediction.probability_up * 100).toFixed(0) }}% рост
      </span>
    </div>
    <div class="no-data" v-else>прогноза пока нет</div>
  </div>
</template>

<script setup>
defineProps({ asset: Object, active: Boolean })
defineEmits(['click'])

function formatPrice(v) {
  return v >= 1 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : v.toFixed(4)
}
function directionLabel(d) {
  return { UP: '↑ Рост', DOWN: '↓ Падение', FLAT: '→ Флэт' }[d] || d
}
</script>
