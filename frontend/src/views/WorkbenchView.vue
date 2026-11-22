<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="meta.key === 'govern_report' && row.status !== '待生成'"
              class="link"
              type="button"
              :data-testid="`review-package-${row.id}`"
              @click="downloadPackage(row)"
            >
              下载复核包
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <section v-if="meta.key === 'quality_monitor'" class="review-panel" data-testid="review-panel">
      <h3>质量报告复核</h3>
      <p class="page-desc">
        选择从治理报告下载的复核包（JSON），读取后生成复查记录；复查记录数可与包内证据明细重新对账。
      </p>
      <div class="review-actions">
        <input type="file" accept=".json,application/json" data-testid="package-file" @change="onPackageFile" />
        <button
          class="btn primary"
          type="button"
          data-testid="submit-package"
          :disabled="!packageText || reviewing"
          @click="submitPackage"
        >
          {{ reviewing ? '复查中…' : '读取复核包并生成复查记录' }}
        </button>
        <span v-if="packageName" class="stat-label">{{ packageName }}</span>
      </div>

      <div v-if="reviewRun" class="stat-row" data-testid="review-run-summary">
        <article class="stat-card">
          <span class="stat-label">复查记录/证据明细</span>
          <strong class="stat-value">{{ reviewRun.review_record_count }}/{{ reviewRun.evidence_count }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">复算失败/包内摘要</span>
          <strong class="stat-value">{{ reviewRun.recomputed_failures }}/{{ reviewRun.summary_failures }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">包摘要校验</span>
          <strong class="stat-value">{{ reviewRun.digest_verified ? '一致' : '不一致' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">轮次分布</span>
          <strong class="stat-value">{{ roundText }}</strong>
        </article>
      </div>
      <p v-if="reviewRun && !reviewRun.counts_match" class="error-text">
        警告：按文件明细复算的指标与包内摘要不一致，请核对文件是否被改动
      </p>
      <p v-if="reviewRun && reviewRun.scope_changed" class="error-text">
        注意：本次复核包与上一轮复查的范围不一致，请与导出尝试记录比对内容摘要
      </p>

      <table v-if="reviewRecords.length" class="data-table" data-testid="review-records">
        <thead>
          <tr>
            <th>复查#</th>
            <th>检查#</th>
            <th>轮次</th>
            <th>目标湖表</th>
            <th>规则</th>
            <th>指标</th>
            <th>阈值</th>
            <th>实际值</th>
            <th>是否通过</th>
            <th>湖表状态</th>
            <th>来源报表</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rec in reviewRecords" :key="rec.review_id" :data-testid="`review-${rec.review_id}`">
            <td>{{ rec.review_id }}</td>
            <td>{{ rec.check_id }}</td>
            <td>{{ rec.round_label }}</td>
            <td>{{ rec.table_name }}</td>
            <td>{{ rec.rule_name }}</td>
            <td>{{ rec.metric }}</td>
            <td>{{ rec.threshold }}</td>
            <td>{{ rec.actual_value }}</td>
            <td>{{ rec.passed ? '通过' : '不通过' }}</td>
            <td>{{ rec.lake_table_status }}</td>
            <td>{{ rec.report_status }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  downloadReviewPackage,
  listEntries,
  listReviewRecords,
  moduleMeta,
  runAction as applyAction,
  submitReviewPackage,
} from '@/api/backend-service'
import type { EntryRow, ReviewRecord, ReviewRunSummary } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
// 质量报告复核：复核包提交与复查记录（仅湖内质量监控模块展示）
const packageText = ref('')
const packageName = ref('')
const reviewing = ref(false)
const reviewRun = ref<ReviewRunSummary | null>(null)
const reviewRecords = ref<ReviewRecord[]>([])
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)
const roundText = computed(() => {
  if (!reviewRun.value) return ''
  return Object.entries(reviewRun.value.rounds)
    .map(([round, count]) => `第${round}轮×${count}`)
    .join(' ')
})

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function downloadPackage(row: EntryRow) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const { version, digest } = await downloadReviewPackage(Number(row.id))
    noticeMessage.value = `复核包已下载：版本=${version}，内容摘要=${digest.slice(0, 23)}…（中断重试后可凭摘要核对复查范围）`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核包导出失败'
  }
}

function onPackageFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  errorMessage.value = ''
  if (!file) return
  packageName.value = file.name
  const reader = new FileReader()
  reader.onload = () => {
    packageText.value = String(reader.result ?? '')
  }
  reader.onerror = () => {
    errorMessage.value = '复核包文件读取失败'
  }
  reader.readAsText(file)
}

async function submitPackage() {
  reviewing.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await submitReviewPackage(packageText.value)
    if (!result.ok) throw new Error(result.message)
    reviewRun.value = (result.entry ?? null) as ReviewRunSummary | null
    noticeMessage.value = result.message
    await reloadReviewRecords()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核包提交失败'
  } finally {
    reviewing.value = false
  }
}

async function reloadReviewRecords() {
  if (meta.key !== 'quality_monitor') return
  try {
    const payload = await listReviewRecords()
    reviewRecords.value = payload.items
  } catch {
    reviewRecords.value = []
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void reloadReviewRecords()
})
</script>
