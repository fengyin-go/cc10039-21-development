/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: Record<string, unknown> | null
}

/** 质量报告复核包：一轮复查的汇总（质量监控读取复核包后返回） */
export type ReviewRunSummary = {
  review_run_id: number
  package_version: string
  package_digest: string
  digest_verified: boolean
  scope_changed: boolean
  report_id: number
  evidence_count: number
  review_record_count: number
  recomputed_failures: number
  summary_failures: number
  counts_match: boolean
  rounds: Record<string, number>
  reviewed_at: string
}

/** 复查记录：一条证据明细在湖内的复查落账 */
export type ReviewRecord = {
  review_id: number
  review_run_id: number
  check_id: number
  check_round: number
  round_label: string
  rule_name: string
  table_name: string
  metric: string
  threshold: string
  actual_value: number
  passed: boolean
  report_status: string
  lake_table_status: string
  reviewed_at: string
}

export type ReviewRecordResult = {
  total: number
  items: ReviewRecord[]
  runs: ReviewRunSummary[]
}

/** 复核包导出尝试：凭内容摘要核对中断重试后复查范围是否变化 */
export type ExportAttempt = {
  attempt_id: number
  report_id: number
  package_version: string
  digest: string
  evidence_count: number
  failures: number
  exported_at: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
