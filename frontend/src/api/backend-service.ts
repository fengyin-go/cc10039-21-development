import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  ExportAttempt,
  OverviewResult,
  PageResult,
  ReviewRecordResult,
  ReviewRunSummary,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/** 下载治理报告的质量复核包，返回版本与内容摘要供交班核对 */
export async function downloadReviewPackage(
  reportId: number,
): Promise<{ version: string; digest: string }> {
  const response = await request(`/api/govern_report/${reportId}/review-package`)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || '复核包导出失败')
  }
  const version = response.headers.get('X-Review-Package-Version') ?? ''
  const digest = response.headers.get('X-Review-Package-Digest') ?? ''
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `govern_report_${reportId}_review_package.json`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
  return { version, digest }
}

/** 查询报告的复核包导出尝试：中断重试后核对复查范围是否变化 */
export async function listExportAttempts(
  reportId: number,
): Promise<{ total: number; items: ExportAttempt[]; scope_changed: boolean }> {
  const response = await request(`/api/govern_report/${reportId}/review-package/attempts`)
  if (!response.ok) throw new Error('导出尝试读取失败')
  return await response.json()
}

/** 把复核包交给湖内质量监控：生成一轮复查记录 */
export async function submitReviewPackage(packageText: string): Promise<ActionResult> {
  let parsed: unknown
  try {
    parsed = JSON.parse(packageText)
  } catch {
    throw new Error('文件不是有效的 JSON，无法作为复核包提交')
  }
  const response = await request('/api/quality_monitor/review-packages', {
    method: 'POST',
    body: JSON.stringify(parsed),
  })
  return await response.json()
}

/** 查询复查记录：值班员把记录数与导出文件里的证据明细重新对一遍 */
export async function listReviewRecords(): Promise<ReviewRecordResult> {
  const response = await request('/api/quality_monitor/review-records')
  if (!response.ok) throw new Error('复查记录读取失败')
  return await response.json()
}
