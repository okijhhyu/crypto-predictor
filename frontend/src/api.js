import axios from 'axios'

const api = axios.create({
  baseURL: 'http://localhost:8000/api/',
})

export default {
  getAssets: () => api.get('assets/').then(r => r.data.results ?? r.data),
  getPredictions: (assetId) => api.get('predictions/', { params: assetId ? { asset: assetId } : {} }).then(r => r.data.results ?? r.data),
  getNews: (assetId) => api.get('news/', { params: assetId ? { asset: assetId } : {} }).then(r => r.data.results ?? r.data),
  getMetrics: (assetId) => api.get('metrics/', { params: assetId ? { asset: assetId } : {} }).then(r => r.data.results ?? r.data),
  getFetchLogs: () => api.get('fetch-logs/').then(r => r.data.results ?? r.data),
  getModelHealth: () => api.get('model-health/').then(r => r.data),
}
