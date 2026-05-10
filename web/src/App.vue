<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { parseSseChunk } from './lib/sse'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

const health = ref({ status: 'unknown', model: '-', vllm_reachable: false })
const metrics = ref(null)
const records = ref([])
const selectedId = ref('')
const filter = ref('all')
const realtimeStatus = ref('connecting')
const errorMessage = ref('')
const isSubmitting = ref(false)

const form = reactive({
  text: '',
  sourceLang: 'ja',
  targetLang: 'zh',
  context: '',
  preserveFormat: true,
})

const newTerm = reactive({
  source: '',
  target: '',
})

const terms = ref([])
let eventSource = null
let reconnectTimer = null
let metricsTimer = null

const languageOptions = [
  { label: '日语', value: 'ja' },
  { label: '英语', value: 'en' },
  { label: '韩语', value: 'ko' },
  { label: '中文', value: 'zh' },
]

const filters = [
  { label: '全部', value: 'all' },
  { label: '进行中', value: 'running' },
  { label: '成功', value: 'completed' },
  { label: '失败', value: 'failed' },
]

const statusLabels = {
  running: '进行中',
  completed: '成功',
  failed: '失败',
}

const filteredRecords = computed(() => {
  if (filter.value === 'all') {
    return records.value
  }
  return records.value.filter((record) => record.status === filter.value)
})

const selectedRecord = computed(() => {
  return records.value.find((record) => record.id === selectedId.value) || filteredRecords.value[0] || null
})

const canSubmit = computed(() => form.text.trim().length > 0 && !isSubmitting.value)

const metricCards = computed(() => {
  const snapshot = metrics.value
  return [
    { label: '活跃请求', value: snapshot?.active_requests ?? 0 },
    { label: '活跃翻译', value: snapshot?.active_translations ?? 0 },
    { label: 'QPS', value: formatNumber(snapshot?.rates?.qps) },
    { label: 'TPS', value: formatNumber(snapshot?.rates?.tps) },
    { label: '平均延迟', value: `${formatNumber(snapshot?.latency_ms?.avg)} ms` },
    { label: '失败', value: snapshot?.lifetime_totals?.translations_failed ?? 0 },
  ]
})

function upsertRecord(record) {
  const index = records.value.findIndex((item) => item.id === record.id)
  if (index >= 0) {
    records.value[index] = record
  } else {
    records.value.unshift(record)
  }

  records.value.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  records.value = records.value.slice(0, 200)

  if (!selectedId.value) {
    selectedId.value = record.id
  }
}

async function loadHealth() {
  try {
    const response = await fetch(`${apiBaseUrl}/health`)
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    health.value = await response.json()
  } catch (error) {
    health.value = { status: 'down', model: '-', vllm_reachable: false }
    errorMessage.value = `服务状态请求失败: ${formatError(error)}`
  }
}

async function loadMetrics() {
  try {
    const response = await fetch(`${apiBaseUrl}/metrics/realtime?window=10`)
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    metrics.value = await response.json()
  } catch (error) {
    errorMessage.value = `指标请求失败: ${formatError(error)}`
  }
}

async function loadHistory() {
  try {
    const response = await fetch(`${apiBaseUrl}/translations/history?limit=100`)
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    const payload = await response.json()
    records.value = payload.records || []
    if (!selectedId.value && records.value.length > 0) {
      selectedId.value = records.value[0].id
    }
  } catch (error) {
    errorMessage.value = `历史记录请求失败: ${formatError(error)}`
  }
}

function connectRealtime() {
  clearTimeout(reconnectTimer)
  if (eventSource) {
    eventSource.close()
  }

  realtimeStatus.value = 'connecting'
  eventSource = new EventSource(`${apiBaseUrl}/translations/realtime`)

  eventSource.onopen = () => {
    realtimeStatus.value = 'connected'
    errorMessage.value = ''
  }

  for (const eventName of ['created', 'updated']) {
    eventSource.addEventListener(eventName, (event) => {
      upsertRecord(JSON.parse(event.data))
    })
  }

  eventSource.onerror = () => {
    realtimeStatus.value = 'disconnected'
    eventSource.close()
    reconnectTimer = setTimeout(connectRealtime, 2000)
  }
}

function addTerm() {
  if (!newTerm.source.trim() || !newTerm.target.trim()) {
    return
  }
  terms.value.push({
    source: newTerm.source.trim(),
    target: newTerm.target.trim(),
  })
  newTerm.source = ''
  newTerm.target = ''
}

function removeTerm(index) {
  terms.value.splice(index, 1)
}

async function submitTestTranslation() {
  if (!canSubmit.value) {
    return
  }

  isSubmitting.value = true
  errorMessage.value = ''

  try {
    const response = await fetch(`${apiBaseUrl}/translate/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: form.text,
        source_lang: form.sourceLang,
        target_lang: form.targetLang,
        context: form.context || null,
        terms: terms.value,
        preserve_format: form.preserveFormat,
      }),
    })

    if (!response.ok || !response.body) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let remainder = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      const parsed = parseSseChunk(remainder, decoder.decode(value, { stream: true }))
      remainder = parsed.remainder
      for (const event of parsed.events) {
        if (event.event === 'error') {
          throw new Error(event.data.message || '流式翻译失败')
        }
      }
    }
  } catch (error) {
    errorMessage.value = `测试翻译失败: ${formatError(error)}`
  } finally {
    isSubmitting.value = false
    await Promise.all([loadMetrics(), loadHistory()])
  }
}

function selectRecord(record) {
  selectedId.value = record.id
}

function statusClass(status) {
  return `status-${status || 'unknown'}`
}

function realtimeLabel(status) {
  if (status === 'connected') {
    return '实时已连接'
  }
  if (status === 'connecting') {
    return '实时连接中'
  }
  return '实时断开'
}

function formatTime(value) {
  if (!value) {
    return '-'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function formatDuration(value) {
  if (value === null || value === undefined) {
    return '-'
  }
  return `${formatNumber(value)} ms`
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '0'
  }
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function formatError(error) {
  return error instanceof Error ? error.message : '未知错误'
}

function preview(value) {
  if (!value) {
    return '空'
  }
  return value.length > 96 ? `${value.slice(0, 96)}...` : value
}

onMounted(async () => {
  await Promise.all([loadHealth(), loadMetrics(), loadHistory()])
  connectRealtime()
  metricsTimer = setInterval(() => {
    loadHealth()
    loadMetrics()
  }, 2000)
})

onBeforeUnmount(() => {
  clearTimeout(reconnectTimer)
  clearInterval(metricsTimer)
  if (eventSource) {
    eventSource.close()
  }
})
</script>

<template>
  <div class="page-shell">
    <header class="top-bar">
      <div>
        <p class="eyebrow">Translation Monitor</p>
        <h1>翻译服务实时监控</h1>
      </div>
      <div class="service-state">
        <span class="state-dot" :class="health.status"></span>
        <div>
          <div class="state-title">{{ health.status }}</div>
          <div class="state-meta">{{ health.model }} / vLLM {{ health.vllm_reachable ? '可达' : '不可达' }}</div>
        </div>
        <span class="realtime-pill" :class="realtimeStatus">{{ realtimeLabel(realtimeStatus) }}</span>
      </div>
    </header>

    <section class="metrics-grid">
      <div v-for="card in metricCards" :key="card.label" class="metric-card">
        <span>{{ card.label }}</span>
        <strong>{{ card.value }}</strong>
      </div>
    </section>

    <main class="monitor-layout">
      <section class="panel history-panel">
        <div class="panel-head">
          <div>
            <h2>翻译记录</h2>
            <p>{{ filteredRecords.length }} / {{ records.length }}</p>
          </div>
          <button class="icon-button" type="button" title="刷新" @click="loadHistory">↻</button>
        </div>

        <div class="filter-row">
          <button
            v-for="item in filters"
            :key="item.value"
            class="filter-button"
            :class="{ active: filter === item.value }"
            type="button"
            @click="filter = item.value"
          >
            {{ item.label }}
          </button>
        </div>

        <div class="record-list">
          <button
            v-for="record in filteredRecords"
            :key="record.id"
            class="record-item"
            :class="{ active: selectedRecord?.id === record.id }"
            type="button"
            @click="selectRecord(record)"
          >
            <span class="record-status" :class="statusClass(record.status)">
              {{ statusLabels[record.status] || record.status }}
            </span>
            <span class="record-main">{{ preview(record.text) }}</span>
            <span class="record-meta">
              {{ record.source_lang }} → {{ record.target_lang }} / {{ formatTime(record.created_at) }}
            </span>
          </button>
          <div v-if="filteredRecords.length === 0" class="empty-state">暂无记录</div>
        </div>
      </section>

      <section class="panel detail-panel">
        <div class="panel-head">
          <div>
            <h2>记录详情</h2>
            <p v-if="selectedRecord">
              {{ statusLabels[selectedRecord.status] || selectedRecord.status }} / {{ formatDuration(selectedRecord.duration_ms) }}
            </p>
          </div>
        </div>

        <div v-if="selectedRecord" class="detail-grid">
          <div class="detail-block">
            <div class="block-label">原文</div>
            <pre>{{ selectedRecord.text }}</pre>
          </div>
          <div class="detail-block">
            <div class="block-label">译文</div>
            <pre>{{ selectedRecord.translation || '等待输出' }}</pre>
          </div>
          <div class="detail-block">
            <div class="block-label">Context</div>
            <pre>{{ selectedRecord.context || '无' }}</pre>
          </div>
          <div class="detail-row">
            <span>语言</span>
            <strong>{{ selectedRecord.source_lang }} → {{ selectedRecord.target_lang }}</strong>
          </div>
          <div class="detail-row">
            <span>模式</span>
            <strong>{{ selectedRecord.is_streaming ? '流式' : '同步' }}</strong>
          </div>
          <div class="detail-row">
            <span>时间</span>
            <strong>{{ formatTime(selectedRecord.created_at) }}</strong>
          </div>
          <div v-if="selectedRecord.terms?.length" class="term-list">
            <span v-for="term in selectedRecord.terms" :key="`${term.source}-${term.target}`">
              {{ term.source }} → {{ term.target }}
            </span>
          </div>
          <div v-if="selectedRecord.error" class="error-banner">{{ selectedRecord.error }}</div>
        </div>
        <div v-else class="empty-state">选择一条记录查看详情</div>
      </section>
    </main>

    <section class="panel test-panel">
      <div class="panel-head">
        <div>
          <h2>测试翻译</h2>
          <p>提交后会进入上方实时记录</p>
        </div>
        <button class="primary-button" type="button" :disabled="!canSubmit" @click="submitTestTranslation">
          {{ isSubmitting ? '翻译中' : '发送' }}
        </button>
      </div>

      <div class="test-grid">
        <label class="field wide">
          <span>原文</span>
          <textarea v-model="form.text" class="text-area" rows="5" />
        </label>
        <label class="field">
          <span>源语言</span>
          <select v-model="form.sourceLang" class="input">
            <option v-for="option in languageOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>目标语言</span>
          <select v-model="form.targetLang" class="input">
            <option v-for="option in languageOptions" :key="`target-${option.value}`" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="field wide">
          <span>Context</span>
          <textarea v-model="form.context" class="text-area small" rows="3" />
        </label>
      </div>

      <div class="term-editor">
        <input v-model="newTerm.source" class="input" placeholder="原术语" />
        <input v-model="newTerm.target" class="input" placeholder="目标译法" />
        <button class="secondary-button" type="button" @click="addTerm">添加术语</button>
        <label class="checkbox-line">
          <input v-model="form.preserveFormat" type="checkbox" />
          <span>保留格式</span>
        </label>
      </div>
      <div v-if="terms.length" class="term-list editable">
        <span v-for="(term, index) in terms" :key="`${term.source}-${term.target}-${index}`">
          {{ term.source }} → {{ term.target }}
          <button type="button" @click="removeTerm(index)">×</button>
        </span>
      </div>
      <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>
    </section>
  </div>
</template>
