<template>
  <section class="page" data-module="schedule">
    <header class="page-head">
      <div>
        <h2>采集调度管理</h2>
        <p class="page-desc">维护调度计划，围绕计划编号、关联任务、调度周期、开始时间做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记调度计划</button>
        <button class="btn" type="button" @click="exportRows">导出采集调度清单</button>
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
            <button class="link" type="button" @click="planNextRound(row)">下一轮待采</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集调度数据，可先登记调度计划</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集调度记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记调度计划</h3>
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

    <Teleport to="body">
      <div v-if="showPlanModal" class="modal-overlay" @click.self="showPlanModal = false">
        <div class="modal-card plan-card">
          <header class="modal-head">
            <h3>下一轮待采任务</h3>
            <button class="btn ghost" type="button" @click="showPlanModal = false">&times;</button>
          </header>
          <div class="modal-body">
            <p class="plan-message">{{ planMessage }}</p>
            <table class="data-table">
              <thead>
                <tr><th>任务类型</th><th>日志序号</th><th>记录标识</th><th>盘点组</th><th>原因</th></tr>
              </thead>
              <tbody>
                <tr v-for="(task, index) in planTasks" :key="index">
                  <td>{{ task.任务类型 }}</td>
                  <td>{{ task.日志序号 }}</td>
                  <td>{{ task.记录标识 }}</td>
                  <td>{{ task.盘点组 }}</td>
                  <td>{{ task.原因 || '—' }}</td>
                </tr>
                <tr v-if="!planTasks.length">
                  <td colspan="5" class="empty-state">下一轮暂无待采任务</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/schedule'
const columns = ["计划编号", "关联任务", "调度周期", "开始时间", "结束时间", "依赖任务", "失败策略", "计划状态"]
const actions = ["确认排期", "启动调度", "暂停调度"]
const statuses = ["草稿", "已排期", "运行中", "已完成", "已暂停"]
const stats = [{"label": "运行中计划", "value": 0}, {"label": "已排期计划", "value": 0}, {"label": "待排期计划", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})
const showPlanModal = ref(false)
const planMessage = ref('')
const planTasks = ref<Array<Record<string, string | number>>>([])

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
      throw new Error('采集调度动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集调度操作失败'
  }
}

async function planNextRound(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/next-round`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const data = await response.json()
    planMessage.value = data.message || ''
    planTasks.value = data.entry?.待采任务 ?? []
    showPlanModal.value = true
    if (data.ok) {
      await reload()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下一轮待采任务生成失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('调度计划列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集调度列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.plan-card {
  width: 560px;
}
.plan-message {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
</style>
