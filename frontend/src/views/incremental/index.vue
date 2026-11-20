<template>
  <section class="page" data-module="incremental">
    <header class="page-head">
      <div>
        <h2>增量采集管理</h2>
        <p class="page-desc">维护增量游标，围绕游标编号、来源标识、增量字段、当前水位做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记增量游标</button>
        <button class="btn" type="button" @click="exportRows">导出增量采集清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="selectCursor(row)">批次</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无增量采集数据，可先登记增量游标</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条增量采集记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="activeCursor" class="batch-panel">
      <header class="page-head">
        <div>
          <h3>增量采集批次 — {{ activeCursor.游标编号 }}（来源 {{ activeCursor.来源标识 }}）</h3>
          <p class="page-desc">
            批次记录本轮已交付的记录标识与还需补采的数据；确认位置推进后，未确认记录仍按原日志序号保留在补采队列。
          </p>
        </div>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="openBatch">开启下一轮批次</button>
          <button class="btn ghost" type="button" @click="closePanel">收起</button>
        </div>
      </header>

      <p v-if="batchMessage" class="batch-message">{{ batchMessage }}</p>

      <div class="batch-grid">
        <div>
          <h4 class="batch-subtitle">来源变更分组（按日志序号）</h4>
          <table class="data-table">
            <thead>
              <tr><th>日志序号</th><th>记录数</th><th>记录标识</th></tr>
            </thead>
            <tbody>
              <tr v-for="group in changeGroups" :key="group.日志序号">
                <td>{{ group.日志序号 }}</td>
                <td>{{ group.记录数 }}</td>
                <td>
                  <span
                    v-for="rec in group.记录"
                    :key="rec.id"
                    class="chip"
                    :class="{ bad: !rec.可读, ok: rec.已交付 }"
                  >
                    {{ rec.记录标识 }}<template v-if="!rec.可读">（读失败）</template>
                    <template v-else-if="rec.已交付">（已交付）</template>
                    <button
                      v-if="!rec.可读"
                      class="link"
                      type="button"
                      @click="repairChange(rec)"
                    >恢复可读</button>
                  </span>
                </td>
              </tr>
              <tr v-if="!changeGroups.length">
                <td colspan="3" class="empty-state">确认位置之后暂无来源变更</td>
              </tr>
            </tbody>
          </table>
          <form class="filter-bar batch-register" @submit.prevent="registerChange">
            <label class="filter-item">
              <span>日志序号</span>
              <input v-model="newChange.日志序号" placeholder="如 6404" required />
            </label>
            <label class="filter-item">
              <span>记录标识</span>
              <input v-model="newChange.记录标识" placeholder="如 BIN-C01" required />
            </label>
            <label class="filter-item">
              <span>盘点组</span>
              <input v-model="newChange.盘点组" placeholder="如 PD-202611-03" required />
            </label>
            <button class="btn" type="submit">登记来源变更</button>
          </form>
        </div>
        <div>
          <h4 class="batch-subtitle">批次列表</h4>
          <table class="data-table">
            <thead>
              <tr>
                <th>批次编号</th><th>轮次</th><th>起始位置</th><th>确认位置</th>
                <th>已交付</th><th>待补采</th><th>状态</th><th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="batch in batches" :key="batch.id">
                <td>{{ batch.批次编号 }}</td>
                <td>{{ batch.轮次 }}</td>
                <td>{{ batch.起始位置 }}</td>
                <td>{{ batch.确认位置 }}</td>
                <td>{{ batch.已交付记录.length }}</td>
                <td>{{ pendingCount(batch) }}</td>
                <td>{{ batch.状态 }}</td>
                <td class="row-actions">
                  <button class="link" type="button" @click="showBatch(batch)">明细</button>
                </td>
              </tr>
              <tr v-if="!batches.length">
                <td colspan="8" class="empty-state">还没有批次，点击「开启下一轮批次」开始</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="activeBatch" class="batch-detail">
        <h4 class="batch-subtitle">
          批次 {{ activeBatch.批次编号 }} 明细（确认位置 {{ activeBatch.确认位置 }}，状态 {{ activeBatch.状态 }}）
        </h4>
        <table class="data-table">
          <thead>
            <tr>
              <th>日志序号</th><th>记录标识</th><th>盘点组</th><th>任务类型</th><th>交付状态</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rec in activeBatch.范围记录" :key="`${rec.日志序号}-${rec.记录标识}`">
              <td>{{ rec.日志序号 }}</td>
              <td>{{ rec.记录标识 }}</td>
              <td>{{ rec.盘点组 }}</td>
              <td>{{ rec.任务类型 }}</td>
              <td>{{ deliveryState(rec) }}</td>
              <td class="row-actions">
                <template v-if="activeBatch.状态 === '采集中' && !isDelivered(rec)">
                  <button class="link" type="button" @click="confirmRecord(rec)">确认交付</button>
                  <button class="link" type="button" @click="failRecord(rec)">标记失败</button>
                </template>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="batch-detail-foot">
          <button
            v-if="activeBatch.状态 === '采集中'"
            class="btn primary"
            type="button"
            @click="advanceBatch"
          >推进确认位置</button>
          <label class="filter-item">
            <span>按日志序号核对未确认记录</span>
            <input v-model="unconfirmedSeq" placeholder="如 6402" @input="loadUnconfirmed" />
          </label>
        </div>
        <table v-if="unconfirmed.length" class="data-table">
          <thead>
            <tr><th>日志序号</th><th>记录标识</th><th>盘点组</th><th>补采状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="rec in unconfirmed" :key="`u-${rec.日志序号}-${rec.记录标识}`">
              <td>{{ rec.日志序号 }}</td>
              <td>{{ rec.记录标识 }}</td>
              <td>{{ rec.盘点组 }}</td>
              <td>{{ rec.补采状态 }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="activeBatch.补采清单.length">
          <h4 class="batch-subtitle">补采清单（保留原记录位置）</h4>
          <table class="data-table">
            <thead>
              <tr>
                <th>原日志序号</th><th>记录标识</th><th>原盘点组</th><th>原因</th><th>状态</th><th>登记批次</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in activeBatch.补采清单" :key="`r-${entry.日志序号}-${entry.记录标识}`">
                <td>{{ entry.日志序号 }}</td>
                <td>{{ entry.记录标识 }}</td>
                <td>{{ entry.盘点组 }}</td>
                <td>{{ entry.原因 }}</td>
                <td>{{ entry.状态 }}</td>
                <td>{{ entry.登记批次 }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记增量游标</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type ChangeRecord = {
  id?: number
  日志序号: number
  记录标识: string
  盘点组?: string
  可读?: boolean
  已交付?: boolean
  任务类型?: string
  补采状态?: string
}

type ChangeGroup = { 日志序号: number; 记录数: number; 记录: ChangeRecord[] }

type RecollectEntry = {
  日志序号: number
  记录标识: string
  盘点组?: string
  原因?: string
  状态?: string
  登记批次?: string
}

type Batch = {
  id: number
  批次编号: string
  游标编号: string
  轮次: number
  起始位置: number
  确认位置: number
  覆盖序号: number[]
  范围记录: ChangeRecord[]
  已交付记录: ChangeRecord[]
  补采清单: RecollectEntry[]
  状态: string
}

const ENDPOINT = '/api/incremental'
const columns = ["游标编号", "来源标识", "增量字段", "当前水位", "批次大小", "采集间隔", "上次采集", "下次采集"]
const actions = ["启动采集", "暂停采集", "标记失败"]
const statuses = ["待启动", "运行中", "已完成", "失败"]
const stats = [{"label": "运行中游标", "value": 0}, {"label": "失败游标", "value": 0}, {"label": "待启动游标", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

const activeCursor = ref<Row | null>(null)
const changeGroups = ref<ChangeGroup[]>([])
const batches = ref<Batch[]>([])
const activeBatch = ref<Batch | null>(null)
const unconfirmed = ref<ChangeRecord[]>([])
const unconfirmedSeq = ref('')
const batchMessage = ref('')
const newChange = ref<Record<string, string>>({ 日志序号: '', 记录标识: '', 盘点组: '' })

function updateStats() {
  const data = rows.value
  stats.length > 0 && (stats[0].value = data.filter((r: Row) => r.status === statuses[1] || r.pending).length)
  stats.length > 1 && (stats[1].value = data.filter((r: Row) => r.status && !r.pending && !r.abnormal).length)
  stats.length > 2 && (stats[2].value = data.filter((r: Row) => r.abnormal).length)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createFields.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || '登记失败')
    }
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('增量采集动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '增量采集操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('增量游标列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
    if (activeCursor.value) {
      activeCursor.value = rows.value.find((r: Row) => r.id === activeCursor.value?.id) ?? activeCursor.value
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '增量采集列表读取失败'
  }
}

async function selectCursor(row: Row) {
  batchMessage.value = ''
  activeCursor.value = row
  activeBatch.value = null
  unconfirmed.value = []
  await Promise.all([loadChanges(), loadBatches()])
}

function closePanel() {
  activeCursor.value = null
  activeBatch.value = null
}

async function loadChanges() {
  if (!activeCursor.value) return
  const response = await request(`${ENDPOINT}/${activeCursor.value.id}/changes`)
  if (response.ok) {
    changeGroups.value = (await response.json()).items ?? []
  }
}

async function loadBatches() {
  if (!activeCursor.value) return
  const code = encodeURIComponent(String(activeCursor.value.游标编号 ?? ''))
  const response = await request(`${ENDPOINT}/batches?游标编号=${code}`)
  if (response.ok) {
    batches.value = (await response.json()).items ?? []
  }
}

async function openBatch() {
  if (!activeCursor.value) return
  batchMessage.value = ''
  const response = await request(`${ENDPOINT}/${activeCursor.value.id}/batches`, {
    method: 'POST',
    body: JSON.stringify({ values: {} }),
  })
  const data = await response.json()
  batchMessage.value = data.message || ''
  if (data.ok && data.entry) {
    activeBatch.value = data.entry
    unconfirmed.value = []
  }
  await Promise.all([loadBatches(), loadChanges(), reload()])
}

async function showBatch(batch: Batch) {
  batchMessage.value = ''
  const response = await request(`${ENDPOINT}/batches/${batch.id}`)
  if (response.ok) {
    activeBatch.value = await response.json()
    await loadUnconfirmed()
  }
}

function isDelivered(rec: ChangeRecord) {
  return !!activeBatch.value?.已交付记录.some(
    (d) => d.日志序号 === rec.日志序号 && d.记录标识 === rec.记录标识,
  )
}

function deliveryState(rec: ChangeRecord) {
  if (isDelivered(rec)) return '已交付'
  const hit = activeBatch.value?.补采清单.find(
    (e) => e.日志序号 === rec.日志序号 && e.记录标识 === rec.记录标识,
  )
  return hit ? hit.状态 ?? '待补采' : '待确认'
}

function pendingCount(batch: Batch) {
  return batch.补采清单.filter((e) => e.状态 === '待补采').length
}

async function postBatchAction(action: string, values: Record<string, unknown>) {
  if (!activeBatch.value) return
  batchMessage.value = ''
  const response = await request(`${ENDPOINT}/batches/${activeBatch.value.id}/${action}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  const data = await response.json()
  batchMessage.value = data.message || ''
  if (data.entry) {
    activeBatch.value = data.entry
  }
  await Promise.all([loadBatches(), loadUnconfirmed()])
}

async function confirmRecord(rec: ChangeRecord) {
  await postBatchAction('confirm', { 记录标识: rec.记录标识, 日志序号: rec.日志序号 })
}

async function failRecord(rec: ChangeRecord) {
  await postBatchAction('fail', { 记录标识: rec.记录标识, 日志序号: rec.日志序号, 原因: '下游交付失败' })
}

async function advanceBatch() {
  if (!activeBatch.value) return
  batchMessage.value = ''
  const response = await request(`${ENDPOINT}/batches/${activeBatch.value.id}/advance`, {
    method: 'POST',
    body: JSON.stringify({ values: {} }),
  })
  const data = await response.json()
  batchMessage.value = data.message || ''
  if (data.entry) {
    activeBatch.value = data.entry
  }
  await Promise.all([loadBatches(), loadChanges(), reload(), loadUnconfirmed()])
}

async function loadUnconfirmed() {
  if (!activeBatch.value) return
  const seq = unconfirmedSeq.value.trim()
  const query = seq ? `?seq=${encodeURIComponent(seq)}` : ''
  const response = await request(`${ENDPOINT}/batches/${activeBatch.value.id}/unconfirmed${query}`)
  if (response.ok) {
    unconfirmed.value = (await response.json()).items ?? []
  }
}

async function repairChange(rec: ChangeRecord) {
  batchMessage.value = ''
  const response = await request(`${ENDPOINT}/changes/${rec.id}/repair`, {
    method: 'POST',
    body: JSON.stringify({ values: {} }),
  })
  const data = await response.json()
  batchMessage.value = data.message || ''
  await loadChanges()
}

async function registerChange() {
  if (!activeCursor.value) return
  batchMessage.value = ''
  const response = await request(`${ENDPOINT}/changes`, {
    method: 'POST',
    body: JSON.stringify({
      values: { ...newChange.value, 来源标识: activeCursor.value.来源标识 },
    }),
  })
  const data = await response.json()
  batchMessage.value = data.message || ''
  if (data.ok) {
    newChange.value = { 日志序号: '', 记录标识: '', 盘点组: '' }
  }
  await loadChanges()
}

onMounted(reload)
</script>

<style scoped>
.batch-panel {
  margin-top: 16px;
  border-top: 2px solid var(--border);
  padding-top: 4px;
}
.batch-message {
  background: #eef4ff;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
}
.batch-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}
.batch-subtitle {
  margin: 12px 0 8px;
  font-size: 14px;
}
.batch-register {
  margin-top: 10px;
}
.batch-detail {
  margin-top: 8px;
}
.batch-detail-foot {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  margin: 10px 0;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 2px 8px;
  margin: 2px 4px 2px 0;
  font-size: 12px;
}
.chip.ok {
  border-color: #12b76a;
  color: #12b76a;
}
.chip.bad {
  border-color: #b42318;
  color: #b42318;
}
@media (max-width: 1100px) {
  .batch-grid {
    grid-template-columns: 1fr;
  }
}
</style>
