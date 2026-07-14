<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'
import { useAuthStore } from '@/stores/auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as convApi from '@/api/conversations'
import { Cpu, Loading } from '@element-plus/icons-vue'
import { renderMarkdown } from '@/utils/markdown'

const route = useRoute()
const router = useRouter()
const store = useConversationStore()
const authStore = useAuthStore()

const inputText = ref('')
const chatContainer = ref<HTMLElement | null>(null)

const convId = ref<string | null>(null)

onMounted(async () => {
  await store.fetchAvailableModels()
  // The watch(…, {immediate:true}) below handles initial conversation load
})

// Cleanup SSE stream on navigation away.
// Research streams are preserved — they keep running on the backend
// and persist results to the DB.
onBeforeUnmount(() => {
  store.stopStreaming()
})

watch(() => route.params.conversationId, async (newId) => {
  const id = newId as string | undefined
  if (id && id !== convId.value) {
    convId.value = id
    await store.fetchConversation(id)
    // If the conversation doesn't exist (404), redirect away
    if (store.error) {
      router.replace('/chat')
      store.clearError()
      return
    }
    // If this conversation had an active research stream that was
    // detached (user navigated away mid-pipeline), poll for new
    // results so the UI catches up without a manual refresh.
    if (store.hasActiveResearch(id)) {
      store.isStreaming = true
      store.phaseLabel = '研究进行中，正在加载最新结果…'
      await reloadMessages(id)
      store.isStreaming = false
      store.phaseLabel = ''
    }
  } else if (!id) {
    convId.value = null
    store.clearCurrentConversation()
  }
}, { immediate: true })

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || store.isStreaming) return
  inputText.value = ''

  let targetId = convId.value
  if (!targetId) {
    const conv = await store.createConversation()
    targetId = conv.id
    convId.value = targetId
    router.replace(`/chat/${targetId}`)
  }

  store.sendMessage(targetId, text)
  scrollToBottom()
}

function handleStop() {
  store.stopStreaming()
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    handleSend()
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

async function handleRejectPlan(messageId: string) {
  try {
    await ElMessageBox.confirm(
      '确定要拒绝此研究计划吗？',
      '确认操作',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return // user cancelled
  }
  try {
    await store.actOnPlan(messageId, 'reject')
  } catch {
    ElMessage.error('操作失败，请重试')
  }
}

// ── Modify plan dialog (B-plan modify router) ─────────────────────────
// The user types a free-text modification; the backend classifies it as
// augment (soft focus notes) vs revise (regenerate the plan). No mode toggle
// here — classification is backend-side (D1).
const modifyDialogVisible = ref(false)
const modifyDialogText = ref('')
const modifyDialogMessageId = ref<string | null>(null)

function openModifyDialog(messageId: string) {
  modifyDialogMessageId.value = messageId
  modifyDialogText.value = ''
  modifyDialogVisible.value = true
}

async function submitModify() {
  const text = modifyDialogText.value.trim()
  const messageId = modifyDialogMessageId.value
  if (!text || !messageId) return
  try {
    await store.actOnPlan(messageId, 'modify', text)
    modifyDialogVisible.value = false
    modifyDialogText.value = ''
  } catch {
    ElMessage.error('修改计划失败，请重试')
    // Keep dialog open on error so the user can retry without losing text.
  }
}


// ── Clarity response state ────────────────────────────────────────────
const clarifyText = ref<Record<string, string>>({})
const clarifySent = ref<Record<string, boolean>>({})
const clarifyLoading = ref<Record<string, boolean>>({})
const gapLoading = ref<Record<string, boolean>>({})
const expandedSections = ref<Record<string, string[]>>({})

// ── Report export state (Phase 8: US6) ────────────────────────────────
const exportLoading = ref<string | null>(null)

async function handleExport(msg: any, fmt: string) {
  const reportId = (msg.metadata as any)?.report_id || (msg.metadata as any)?.reportId
  if (!reportId) return
  const key = `${reportId}-${fmt}`
  exportLoading.value = key
  try {
    const url = `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'}/reports/${reportId}/export?format=${fmt}`
    const token = localStorage.getItem('access_token')
    const resp = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}))
      throw new Error((errBody as any).detail || `HTTP ${resp.status}`)
    }
    const blob = await resp.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    const disposition = resp.headers.get('Content-Disposition') || ''
    const star = disposition.match(/filename\*=UTF-8''(.+)/)
    const plain = disposition.match(/filename="?([^"]+)"?/)
    a.download = star
      ? decodeURIComponent(star[1])
      : plain?.[1] || `report.${fmt === 'pdf' ? 'pdf' : 'md'}`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) {
    console.error('[export] failed:', e)
    ElMessage.error('导出失败，请稍后重试')
  } finally {
    exportLoading.value = null
  }
}

// ── Citation popup state (Phase 6: US4) ────────────────────────────────
const citePopupVisible = ref(false)
const citePopupDetail = ref<Record<string, unknown> | null>(null)
const citePopupLoading = ref(false)
const citePopupPosition = ref({ x: 0, y: 0 })

import { getCitationDetail } from '@/api/research'

async function handleCiteClick(event: MouseEvent, msg: { metadata?: Record<string, unknown> }) {
  const target = event.target as HTMLElement | null
  const marker = target?.closest?.('.cite-marker') as HTMLElement | null
  if (!marker) return
  const indices = marker.dataset.cite || ''
  const firstIdx = parseInt(indices.split(/[,-]/)[0], 10)
  if (!firstIdx) return
  const reportId = (msg.metadata?.report_id || msg.metadata?.reportId) as string
  if (!reportId) return

  citePopupLoading.value = true
  citePopupPosition.value = { x: event.clientX, y: event.clientY }
  citePopupVisible.value = true
  try {
    const detail = await getCitationDetail(reportId, firstIdx)
    citePopupDetail.value = detail as unknown as Record<string, unknown>
  } catch {
    citePopupDetail.value = { index: firstIdx, text: '无法加载引用详情' }
  }
  citePopupLoading.value = false
}

async function handleAcceptPlan(messageId: string) {
  try {
    await store.actOnPlan(messageId, 'accept')
  } catch {
    ElMessage.error('操作失败，请重试')
  }
}

async function handleClarifyResponse(msg: { id: string; metadata?: { taskId?: string } }) {
  const taskId = msg.metadata?.taskId
  const text = (clarifyText.value[msg.id] || '').trim()
  if (!taskId || !text) return

  clarifyLoading.value = { ...clarifyLoading.value, [msg.id]: true }
  try {
    await convApi.clarifyTask(taskId, text)
    clarifySent.value = { ...clarifySent.value, [msg.id]: true }
    clarifyLoading.value = { ...clarifyLoading.value, [msg.id]: false }
  } catch (e) {
    console.error('[ChatPage] clarify failed:', e)
    ElMessage.error('澄清提交失败，请重试')
    clarifyLoading.value = { ...clarifyLoading.value, [msg.id]: false }
  }
}

async function handleGapAction(msg: { id: string; metadata?: { taskId?: string } }, action: 'answer' | 'skip') {
  const taskId = msg.metadata?.taskId
  if (!taskId || !convId.value) return
  gapLoading.value = { ...gapLoading.value, [msg.id]: true }
  try {
    await convApi.actOnGap(taskId, action, convId.value)
    const target = store.messages.find(m => m.id === msg.id)
    if (target) target.metadata = { ...target.metadata, status: action === 'answer' ? 'answered' : 'skipped' }

    // The graph now runs synchronously — when actOnGap returns the DB is
    // already updated.  Reload messages to show the new cards immediately.
    if (convId.value) {
      await reloadMessages(convId.value)
    }
  } catch (e) {
    console.error('[ChatPage] gap action failed:', e)
    ElMessage.error('操作失败，请重试')
  }
  gapLoading.value = { ...gapLoading.value, [msg.id]: false }
}

// ── Reload messages from API (used after graph-resume completes) ────────
async function reloadMessages(convId: string) {
  try {
    const result = await convApi.fetchMessages(convId)
    // Preserve local status flags (gap answered/skipped, clarify sent)
    const local = new Map<string, Record<string, unknown>>()
    for (const m of store.messages) {
      if (m.metadata?.status) local.set(m.id, { status: m.metadata.status } as Record<string, unknown>)
    }
    store.messages.splice(0, store.messages.length)
    for (const m of result.items) {
      const saved = local.get(m.id)
      if (saved) m.metadata = { ...m.metadata, ...saved }
      store.messages.push(m)
    }
  } catch (e) {
    console.error('[ChatPage] reload messages failed:', e)
  }
}

// Render an inline-citation marker [N] as a small clickable-looking badge.
// Full citation popup is Phase 6 (US4); here we just style the marker so
// users can cross-reference the citation list below the report.
function credibilityTagType(cred: string): 'success' | 'warning' | 'info' | 'danger' {
  if (cred === 'high') return 'success'
  if (cred === 'medium') return 'warning'
  if (cred === 'low') return 'danger'
  return 'info'
}

// Throttled scroll during streaming (uses rAF to avoid layout thrashing)
let _scrollRafId = 0
function throttledScroll() {
  if (_scrollRafId) return
  _scrollRafId = requestAnimationFrame(() => {
    _scrollRafId = 0
    scrollToBottom()
  })
}

watch(() => store.streamingContent, throttledScroll)
watch(() => store.messages.length, scrollToBottom)
</script>

<template>
  <div class="chat-page">
    <div class="chat-messages" ref="chatContainer">
      <!-- Welcome / Empty state -->
      <div v-if="!store.messages.length && !store.streamingContent" class="welcome">
        <h2>深度研究平台</h2>
        <p v-if="!convId">输入研究主题或任意问题开始对话</p>
        <p v-else>发送第一条消息开始对话</p>
      </div>

      <!-- Loading skeletons (T121) -->
      <div v-if="store.loading" class="loading-state">
        <div class="message-row assistant">
          <div class="skeleton-avatar"></div>
          <div class="msg-bubble skeleton-bubble" style="width:360px;height:60px"></div>
        </div>
        <div class="message-row user">
          <div class="msg-bubble skeleton-bubble" style="width:240px;height:40px;background:var(--skeleton-user-bg)"></div>
          <div class="skeleton-avatar"></div>
        </div>
        <div class="message-row assistant">
          <div class="skeleton-avatar"></div>
          <div class="msg-bubble skeleton-bubble" style="width:420px;height:80px"></div>
        </div>
        <div class="message-row user">
          <div class="msg-bubble skeleton-bubble" style="width:180px;height:36px;background:var(--skeleton-user-bg)"></div>
          <div class="skeleton-avatar"></div>
        </div>
      </div>

      <!-- Messages -->
      <template v-for="msg in store.messages" :key="msg.id">
        <!-- User message: bubble left, avatar right -->
        <div v-if="msg.role === 'user'" class="message-row user">
          <div class="msg-bubble-wrapper">
            <div class="msg-bubble user-bubble" v-if="msg.messageType === 'text'">
              {{ msg.content }}
            </div>
            <!-- Plan card -->
            <div class="msg-card plan" v-else-if="msg.messageType === 'plan_card'">
              <h4>研究计划</h4>
              <ul v-if="Array.isArray(msg.metadata?.questions)">
                <li v-for="(q, idx) in (msg.metadata?.questions as string[])" :key="idx">{{ q }}</li>
              </ul>
              <p v-if="Array.isArray(msg.metadata?.keywords)">
                <el-tag
                  v-for="kw in (msg.metadata?.keywords as string[])"
                  :key="kw" size="small" style="margin-right:4px"
                >{{ kw }}</el-tag>
              </p>
              <pre
                v-if="msg.content && msg.content !== 'Research plan generated'"
                style="white-space:pre-wrap;opacity:0.7"
              >{{ msg.content }}</pre>
              <div
                class="plan-actions"
                v-if="msg.metadata?.status === 'pending_confirmation' || msg.metadata?.status === 'revised'"
              >
                <el-button type="primary" size="small" @click="handleAcceptPlan(msg.id)">接受</el-button>
                <el-button size="small" @click="openModifyDialog(msg.id)">修改</el-button>
                <el-button size="small" type="danger" @click="handleRejectPlan(msg.id)">拒绝</el-button>
              </div>
              <el-tag
                v-else-if="msg.metadata?.status === 'accepted_with_notes'"
                type="success" size="small" style="margin-top:8px"
              >已采纳补充：{{ msg.metadata?.user_focus_notes || '' }}</el-tag>
            </div>
            <!-- Report card -->
            <div
              class="msg-card report"
              v-else-if="msg.messageType === 'report_card'"
              @click="handleCiteClick($event, msg)"
            >
              <p>研究报告已生成</p>
            </div>
          </div>
          <el-avatar :size="34" class="msg-avatar user-avatar">
            {{ (authStore.userName || 'U').charAt(0).toUpperCase() }}
          </el-avatar>
        </div>

        <!-- AI message: avatar left, bubble right -->
        <div v-else class="message-row assistant">
          <el-avatar :size="34" class="msg-avatar ai-avatar">
            <el-icon :size="18"><Cpu /></el-icon>
          </el-avatar>
          <div class="msg-bubble-wrapper">
            <div class="msg-bubble ai-bubble markdown-body" v-if="msg.messageType === 'text'" v-html="renderMarkdown(String(msg.content))"></div>
            <!-- Knowledge-base source references -->
            <div v-if="msg.messageType === 'text' && Array.isArray(msg.metadata?.kb_sources) && (msg.metadata?.kb_sources as any[]).length" class="kb-refs">
              <el-collapse>
                <el-collapse-item :title="`📚 参考知识库（${(msg.metadata?.kb_sources as any[]).length} 个片段）`">
                  <div v-for="(src, i) in (msg.metadata?.kb_sources as any[])" :key="i" class="kb-ref-item">
                    <div class="kb-ref-header">
                      <span class="kb-ref-index">[{{ i + 1 }}]</span>
                      <span class="kb-ref-file">{{ src.filename }}</span>
                      <span v-if="src.chunkIndex !== undefined" class="kb-ref-chunk">片段 #{{ src.chunkIndex }}</span>
                    </div>
                    <div class="kb-ref-excerpt">{{ (src.excerpt || '').slice(0, 300) }}{{ (src.excerpt || '').length > 300 ? '...' : '' }}</div>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
            <!-- Plan card -->
            <div class="msg-card plan" v-else-if="msg.messageType === 'plan_card'">
              <h4>研究计划</h4>
              <ul v-if="Array.isArray(msg.metadata?.questions)">
                <li v-for="(q, idx) in (msg.metadata?.questions as any[])" :key="idx">
                  <template v-if="typeof q === 'string'">{{ q }}</template>
                  <template v-else>
                    {{ q.question }}
                    <ul v-if="Array.isArray(q.sub_questions) && q.sub_questions.length">
                      <li v-for="(sq, sidx) in q.sub_questions" :key="sidx">
                        {{ typeof sq === 'string' ? sq : sq.question }}
                      </li>
                    </ul>
                  </template>
                </li>
              </ul>
              <p v-if="Array.isArray(msg.metadata?.keywords)">
                <el-tag
                  v-for="kw in (msg.metadata?.keywords as string[])"
                  :key="kw" size="small" style="margin-right:4px"
                >{{ kw }}</el-tag>
              </p>
              <pre
                v-if="msg.content && msg.content !== 'Research plan generated'"
                style="white-space:pre-wrap;opacity:0.7"
              >{{ msg.content }}</pre>
              <div
                class="plan-actions"
                v-if="msg.metadata?.status === 'pending_confirmation' || msg.metadata?.status === 'revised'"
              >
                <el-button type="primary" size="small" @click="handleAcceptPlan(msg.id)">接受</el-button>
                <el-button size="small" @click="openModifyDialog(msg.id)">修改</el-button>
                <el-button size="small" type="danger" @click="handleRejectPlan(msg.id)">拒绝</el-button>
              </div>
              <el-tag
                v-else-if="msg.metadata?.status === 'accepted'"
                type="success" size="small" style="margin-top:8px"
              >已接受</el-tag>
              <el-tag
                v-else-if="msg.metadata?.status === 'accepted_with_notes'"
                type="success" size="small" style="margin-top:8px"
              >已采纳补充：{{ msg.metadata?.user_focus_notes || '' }}</el-tag>
              <el-tag
                v-else-if="msg.metadata?.status === 'rejected'"
                type="info" size="small" style="margin-top:8px"
              >已拒绝</el-tag>
            </div>
            <!-- Retrieval card -->
            <div
              class="msg-card retrieval"
              v-else-if="msg.messageType === 'retrieval_card'"
            >
              <h4>资料检索 · 第 {{ msg.metadata?.round || 1 }} 轮</h4>
              <p class="retrieval-count">
                共检索到 {{ msg.metadata?.sourceCount || msg.metadata?.source_count || 0 }} 条来源
              </p>
              <el-collapse v-if="Array.isArray(msg.metadata?.sources) && (msg.metadata?.sources as any[]).length">
                <el-collapse-item
                  :title="`查看 ${(msg.metadata?.sources as any[]).length} 条来源`"
                  :name="msg.id"
                >
                  <ul class="source-list">
                    <li
                      v-for="(src, sidx) in (msg.metadata?.sources as any[])"
                      :key="sidx"
                    >
                      <el-tag
                        size="small"
                        :type="credibilityTagType(src.credibility)"
                        style="margin-right:6px"
                      >{{ src.credibility }}</el-tag>
                      <span class="source-type">{{ src.sourceType }}</span>
                      <a
                        v-if="src.url" :href="src.url" target="_blank"
                        rel="noopener noreferrer" class="source-title"
                      >{{ src.title || src.url }}</a>
                      <span v-else class="source-title">{{ src.title }}</span>
                    </li>
                  </ul>
                </el-collapse-item>
              </el-collapse>
            </div>
            <!-- Analysis result card -->
            <div
              class="msg-card analysis"
              v-else-if="msg.messageType === 'analysis_card'"
            >
              <h4>知识整合 · 第 {{ msg.metadata?.round || 1 }} 轮</h4>
              <div class="analysis-stats" style="display:flex; gap:12px; margin:8px 0">
                <el-tag type="warning" size="small">知识缺口: {{ msg.metadata?.gapCount || msg.metadata?.gap_count || 0 }}</el-tag>
                <el-tag v-if="msg.metadata?.criticalCount || msg.metadata?.critical_count" type="danger" size="small">严重缺口: {{ msg.metadata?.criticalCount || msg.metadata?.critical_count }}</el-tag>
              </div>
              <el-collapse v-if="msg.metadata?.summaryPreview || msg.metadata?.summary_preview">
                <el-collapse-item title="查看知识整合详情" :name="`analysis-${msg.id}`">
                  <div class="analysis-preview markdown-body" v-html="renderMarkdown(String(msg.metadata?.summaryPreview || msg.metadata?.summary_preview))"></div>
                </el-collapse-item>
              </el-collapse>
            </div>
            <!-- Clarifying question -->
            <div
              class="msg-card clarifying"
              v-else-if="msg.messageType === 'clarifying_question'"
            >
              <h4>让我们澄清一下研究主题</h4>
              <p>{{ msg.content }}</p>
              <div class="clarify-input" v-if="!clarifySent[msg.id] && msg.metadata?.status !== 'clarified'">
                <el-input
                  v-model="clarifyText[msg.id]"
                  size="small"
                  placeholder="在这里输入更具体的主题..."
                  :disabled="!!clarifyLoading[msg.id]"
                />
                <el-button
                  type="primary"
                  size="small"
                  :loading="!!clarifyLoading[msg.id]"
                  :disabled="!clarifyText[msg.id]"
                  @click="handleClarifyResponse(msg)"
                  style="margin-top:6px"
                >提交</el-button>
              </div>
              <el-tag v-else type="success" size="small" style="margin-top:8px">
                已澄清，正在继续研究...
              </el-tag>
            </div>
            <!-- Gap question card -->
            <div
              class="msg-card gap"
              v-else-if="msg.messageType === 'gap_question'"
            >
              <h4>知识缺口 · 第 {{ msg.metadata?.round || 1 }} 轮</h4>
              <p class="gap-hint">以下问题暂未找到充分资料，是否补充检索？</p>
              <ul v-if="Array.isArray(msg.metadata?.gaps)" class="gap-list">
                <li v-for="(g, gidx) in (msg.metadata?.gaps as any[])" :key="gidx">
                  <el-tag :type="g.severity === 'critical' ? 'danger' : 'warning'" size="small" style="margin-right:6px">{{ g.severity }}</el-tag>
                  <span>{{ g.description }}</span>
                </li>
              </ul>
              <div class="gap-actions" v-if="msg.metadata?.status === 'pending'">
                <el-button type="primary" size="small" :disabled="!!gapLoading[msg.id]" :loading="!!gapLoading[msg.id]" @click="handleGapAction(msg, 'answer')">补充检索</el-button>
                <el-button size="small" :disabled="!!gapLoading[msg.id]" @click="handleGapAction(msg, 'skip')">跳过</el-button>
              </div>
              <el-tag v-else-if="msg.metadata?.status === 'answered'" type="success" size="small" style="margin-top:8px">已补充检索</el-tag>
              <el-tag v-else-if="msg.metadata?.status === 'skipped'" type="info" size="small" style="margin-top:8px">已跳过</el-tag>
            </div>
            <!-- Report card -->
            <div
              class="msg-card report"
              v-else-if="msg.messageType === 'report_card'"
              @click="handleCiteClick($event, msg)"
            >
              <div class="report-header">
                <h4>{{ msg.metadata?.title || '研究报告' }}</h4>
                <div class="report-actions" v-if="msg.metadata?.report_id || msg.metadata?.reportId">
                  <el-button
                    size="small" text type="primary"
                    :loading="exportLoading === `${msg.metadata?.report_id || msg.metadata?.reportId}-md`"
                    @click.stop="handleExport(msg, 'markdown')"
                  >导出 Markdown</el-button>
                  <el-button
                    size="small" text type="primary"
                    :loading="exportLoading === `${msg.metadata?.report_id || msg.metadata?.reportId}-pdf`"
                    @click.stop="handleExport(msg, 'pdf')"
                  >导出 PDF</el-button>
                </div>
              </div>
              <div
                v-if="msg.metadata?.abstract"
                class="report-abstract markdown-body"
                v-html="renderMarkdown(String(msg.metadata?.abstract))"
              ></div>
              <div
                v-if="Array.isArray(msg.metadata?.sections) && (msg.metadata?.sections as any[]).length"
                class="report-sections"
              >
                <el-collapse v-model="expandedSections[msg.id]">
                  <el-collapse-item
                    v-for="(section, sidx) in (msg.metadata?.sections as any[])"
                    :key="sidx"
                    :name="`${msg.id}-${sidx}`"
                  >
                    <template #title>
                      <span class="section-heading">{{ section.heading }}</span>
                    </template>
                    <div
                      class="report-section-content markdown-body"
                      v-html="renderMarkdown(String(section.content))"
                    ></div>
                  </el-collapse-item>
                </el-collapse>
              </div>
              <div
                v-if="msg.metadata?.gap_notes"
                class="report-gap-notes"
              >
                <h5>知识缺口说明</h5>
                <div
                  class="markdown-body"
                  v-html="renderMarkdown(String(msg.metadata?.gap_notes))"
                ></div>
              </div>
              <div
                v-if="Array.isArray(msg.metadata?.citations) && (msg.metadata?.citations as any[]).length"
                class="report-citations"
              >
                <h5>引用列表</h5>
                <ol>
                  <li
                    v-for="(cite, cidx) in (msg.metadata?.citations as any[])"
                    :key="cidx"
                  >
                    <el-tag
                      size="small"
                      :type="credibilityTagType(cite.credibility)"
                      style="margin-right:6px"
                    >{{ cite.credibility }}</el-tag>
                    <el-tag
                      v-if="cite.sourceType"
                      size="small"
                      type="info"
                      style="margin-right:6px"
                    >{{ cite.sourceType }}</el-tag>
                    <a
                      v-if="cite.url"
                      :href="cite.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="source-title"
                    >{{ cite.title || cite.url }}</a>
                    <span v-else class="source-title">{{ cite.title || cite.text }}</span>
                  </li>
                </ol>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- Thinking indicator: shown when waiting for first token / phase -->
      <div v-if="store.isStreaming && !store.streamingContent" class="message-row assistant">
        <el-avatar :size="34" class="msg-avatar ai-avatar">
          <el-icon :size="18"><Cpu /></el-icon>
        </el-avatar>
        <div class="msg-bubble thinking">
          <div class="thinking-dots">
            <span></span><span></span><span></span>
          </div>
          <span class="thinking-text">{{ store.phaseLabel || '正在思考...' }}</span>
        </div>
      </div>

      <!-- Streaming indicator -->
      <div v-if="store.streamingContent" class="message-row assistant">
        <el-avatar :size="34" class="msg-avatar ai-avatar">
          <el-icon :size="18"><Cpu /></el-icon>
        </el-avatar>
        <div class="msg-bubble streaming-bubble">
          {{ store.streamingContent }}
          <span class="cursor">|</span>
        </div>
      </div>

      <!-- Error display -->
      <div v-if="store.error" class="message-row assistant">
        <el-avatar :size="34" class="msg-avatar ai-avatar">
          <el-icon :size="18"><Cpu /></el-icon>
        </el-avatar>
        <div class="msg-bubble error-bubble">
          <span>{{ store.error }}</span>
          <el-button size="small" text @click="store.clearError()">关闭</el-button>
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <div class="model-bar">
        <el-select
          v-model="store.selectedModel"
          size="small"
          :disabled="store.isStreaming"
        >
          <el-option
            v-for="m in store.availableModels"
            :key="m"
            :label="m"
            :value="m"
          />
        </el-select>
        <el-switch
          v-model="store.mode"
          size="small"
          active-value="research"
          inactive-value="chat"
          :disabled="store.isStreaming"
          inline-prompt
          active-text="深度研究"
          inactive-text="对话"
        />
        <el-switch
          v-model="store.useKnowledge"
          size="small"
          :disabled="store.isStreaming"
          inline-prompt
          active-text="知识库"
          inactive-text="知识库"
        />
      </div>
      <div class="chat-input">
        <el-input
          v-model="inputText"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 5 }"
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
          :disabled="store.isStreaming"
          @keydown="handleKeydown"
        />
        <el-button
          v-if="!store.isStreaming"
          type="primary"
          :disabled="!inputText.trim()"
          @click="handleSend"
        >
          发送
        </el-button>
        <el-button
          v-else
          type="danger"
          @click="handleStop"
        >
          停止
        </el-button>
      </div>
    </div>

    <!-- Modify plan dialog (B-plan modify router) -->
    <el-dialog
      v-model="modifyDialogVisible"
      title="修改研究计划"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="modifyDialogText"
        type="textarea"
        :rows="4"
        placeholder="如：多关注安全性 / 不要性能，换成成本分析"
        maxlength="1000"
        show-word-limit
      />
      <template #footer>
        <el-button @click="modifyDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!modifyDialogText.trim()"
          @click="submitModify"
        >确认修改</el-button>
      </template>
    </el-dialog>

    <!-- Citation popup (Phase 6: US4) -->
    <teleport to="body">
      <div
        v-if="citePopupVisible"
        class="cite-popup-overlay"
        @click.self="citePopupVisible = false"
      >
        <div
          class="cite-popup"
          :style="{ left: citePopupPosition.x + 'px', top: citePopupPosition.y + 'px' }"
        >
          <div class="cite-popup-header">
            <span>引用 #{{ citePopupDetail?.index }}</span>
            <el-button size="small" text @click="citePopupVisible = false">✕</el-button>
          </div>
          <div v-if="citePopupLoading" class="cite-popup-loading">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>加载中…</span>
          </div>
          <div v-else-if="citePopupDetail" class="cite-popup-body">
            <div class="cite-source-title">{{ (citePopupDetail.source as any)?.title || citePopupDetail.text }}</div>
            <el-tag
              size="small"
              :type="credibilityTagType(String((citePopupDetail.source as any)?.credibility || citePopupDetail.credibility || 'medium'))"
              style="margin: 8px 0"
            >{{ (citePopupDetail.source as any)?.credibility || citePopupDetail.credibility || 'medium' }}</el-tag>
            <div v-if="(citePopupDetail.source as any)?.sourceType" style="margin: 4px 0; color: #909399; font-size: 12px">
              来源类型: {{ (citePopupDetail.source as any).sourceType }}
            </div>
            <div v-if="(citePopupDetail.source as any)?.authors?.length">
              <span style="font-size:12px;color:#909399">作者: </span>
              <span style="font-size:12px">{{ (citePopupDetail.source as any).authors.join(', ') }}</span>
            </div>
            <div v-if="(citePopupDetail.source as any)?.publicationDate" style="color:#909399;font-size:12px;margin:4px 0">
              日期: {{ (citePopupDetail.source as any).publicationDate }}
            </div>
            <div v-if="(citePopupDetail.source as any)?.url" style="margin:6px 0">
              <a :href="(citePopupDetail.source as any).url" target="_blank" rel="noopener noreferrer" style="font-size:12px">
                {{ (citePopupDetail.source as any).url }}
              </a>
            </div>
            <div v-if="(citePopupDetail.source as any)?.abstract" class="cite-abstract">
              <div style="font-size:12px;color:#909399;margin-bottom:4px">摘要</div>
              <div style="font-size:12px;line-height:1.6;max-height:150px;overflow-y:auto;white-space:pre-wrap">
                {{ (citePopupDetail.source as any).abstract }}
              </div>
            </div>
            <div v-if="(citePopupDetail.source as any)?.rawContentAvailable" style="margin-top:8px">
              <el-tag size="small" type="success">原始内容已缓存</el-tag>
            </div>
          </div>
        </div>
      </div>
    </teleport>
  </div>
</template>

<style scoped>
/* ── Page Layout ──────────────────────────────────────────────────── */
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 960px;
  margin: 0 auto;
}

/* ── Messages Area ────────────────────────────────────────────────── */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* ── Welcome & Loading ────────────────────────────────────────────── */
.welcome {
  text-align: center;
  margin-top: 30vh;
  color: #909399;
}
.welcome h2 { font-size: 24px; margin-bottom: 8px; }

.loading-state {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 20px 0;
}

.skeleton-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #e8eaed;
  flex-shrink: 0;
  animation: skeleton-shimmer 1.5s infinite;
}

.skeleton-bubble {
  --skeleton-user-bg: #d5e3ff;
  background: #e8eaed;
  border-radius: 12px;
  animation: skeleton-shimmer 1.5s infinite;
}

@keyframes skeleton-shimmer {
  0%   { opacity: 0.4; }
  50%  { opacity: 0.8; }
  100% { opacity: 0.4; }
}

/* ── Message Row (avatar + bubble) ────────────────────────────────── */
.message-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 16px;
}
.message-row.user {
  justify-content: flex-end;
}
.message-row.assistant {
  justify-content: flex-start;
}

/* ── Avatars ──────────────────────────────────────────────────────── */
.msg-avatar {
  flex-shrink: 0;
}
.ai-avatar {
  background: #409eff;
  color: #fff;
}
.user-avatar {
  background: #67c23a;
  color: #fff;
}

/* ── Message Bubble ───────────────────────────────────────────────── */
.msg-bubble-wrapper {
  display: contents;
}

/* ── Knowledge-base refs ─────────────────────────────────────────── */
.kb-refs {
  max-width: min(80%, 720px);
  margin-top: 0;
}
.kb-refs :deep(.el-collapse-item__header) {
  font-size: 12px;
  color: #67c23a;
  height: auto;
  padding: 6px 0;
}
.kb-ref-item {
  font-size: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed #ebeef5;
}
.kb-ref-item:last-child { border-bottom: none; }
.kb-ref-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}
.kb-ref-index { font-weight: 600; color: #67c23a; }
.kb-ref-file { font-weight: 500; color: #606266; }
.kb-ref-chunk { color: #c0c4cc; font-size: 11px; }
.kb-ref-excerpt {
  color: #909399;
  line-height: 1.5;
  margin-top: 2px;
}
.msg-bubble {
  padding: 10px 14px;
  border-radius: 12px;
  line-height: 1.6;
  max-width: min(80%, 720px);
  word-break: break-word;
  width: fit-content;
}
.user-bubble {
  background: #ecf5ff;
}
.ai-bubble {
  background: #f5f7fa;
}
.error-bubble {
  background: #fef0f0;
  color: #f56c6c;
  display: flex;
  align-items: center;
  gap: 8px;
}
.streaming-bubble {
  background: #f5f7fa;
}

/* ── Thinking Dots ────────────────────────────────────────────────── */
.thinking {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #f5f7fa;
}
.thinking-text {
  color: #909399;
  font-size: 14px;
}
.thinking-dots span {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #909399;
  margin-right: 4px;
  animation: dot-bounce 1.4s infinite ease-in-out both;
}
.thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
.thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
@keyframes dot-bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

/* ── Blinking Cursor ──────────────────────────────────────────────── */
.cursor {
  animation: blink 1s infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .cursor { animation: none; }
  .thinking-dots span { animation: none; }
}

/* ── Message Cards (plan / report) ────────────────────────────────── */
/* ── Analysis card collapse: prevent content clipping ────────── */
.msg-card.analysis :deep(.el-collapse-item__wrap) {
  max-height: none;
  overflow: visible;
}
.msg-card.analysis :deep(.el-collapse-item__content) {
  max-height: none;
  overflow: visible;
  padding-bottom: 12px;
}
.analysis-preview {
  max-height: 60vh;
  overflow-y: auto;
}

.msg-card {
  padding: 16px;
  background: #f5f7fa;
  border-radius: 12px;
  border: 1px solid #e4e7ed;
  margin-bottom: 8px;
  max-width: min(80%, 720px);
}
.plan-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}

/* ── Retrieval / Gap / Report Cards (Phase 4') ─────────────────────── */
.msg-card h4 {
  margin: 0 0 8px;
  font-size: 15px;
}
.msg-card h5 {
  margin: 12px 0 6px;
  font-size: 13px;
  color: #606266;
}
.retrieval-count {
  color: #909399;
  font-size: 13px;
  margin: 0 0 6px;
}
.source-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.source-list li {
  padding: 4px 0;
  font-size: 13px;
  line-height: 1.5;
  border-bottom: 1px dashed #ebeef5;
}
.source-list li:last-child {
  border-bottom: none;
}
.source-type {
  color: #909399;
  margin-right: 6px;
  font-size: 12px;
}
.source-title {
  color: #409eff;
}
.report-header h4 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 12px;
  color: #1d2129;
}
.report-abstract {
  color: #4e5969;
  font-size: 14px;
  line-height: 1.7;
  margin: 0 0 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e5e6eb;
}
.report-sections {
  margin: 12px 0;
}
.report-sections :deep(.el-collapse-item__header) {
  font-weight: 600;
  font-size: 14px;
  color: #1d2129;
  height: auto;
  line-height: 1.5;
  padding: 10px 0;
}
.report-sections :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}
.section-heading {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
}
.report-section-content {
  font-size: 14px;
  line-height: 1.8;
}
.report-gap-notes {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid #ebeef5;
  font-size: 13px;
  color: #909399;
}
.report-citations {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid #ebeef5;
}
.report-citations ol {
  padding-left: 20px;
  font-size: 12px;
  color: #606266;
  line-height: 1.7;
}

/* ── Markdown Body ──────────────────────────────────────────────────── */
.markdown-body {
  color: #1d2129;
  line-height: 1.8;
  word-break: break-word;
}
.markdown-body h1, .markdown-body h2, .markdown-body h3,
.markdown-body h4, .markdown-body h5, .markdown-body h6 {
  margin: 16px 0 8px;
  font-weight: 600;
  color: #1d2129;
}
.markdown-body h1 { font-size: 1.4em; }
.markdown-body h2 { font-size: 1.25em; border-bottom: 1px solid #e5e6eb; padding-bottom: 4px; }
.markdown-body h3 { font-size: 1.1em; }
.markdown-body h4 { font-size: 1.05em; }
.markdown-body p {
  margin: 0 0 10px;
}
.markdown-body ul, .markdown-body ol {
  padding-left: 24px;
  margin: 6px 0 12px;
}
.markdown-body li {
  margin: 2px 0;
}
.markdown-body blockquote {
  border-left: 3px solid #409eff;
  padding: 4px 12px;
  margin: 8px 0;
  color: #606266;
  background: #f0f5ff;
  border-radius: 0 4px 4px 0;
}
.markdown-body code {
  background: #f2f3f5;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.9em;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  color: #e74c3c;
}
.markdown-body pre {
  background: #1d2129;
  color: #e8e8e8;
  padding: 12px 16px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
  margin: 8px 0 16px;
}
.markdown-body pre code {
  background: none;
  padding: 0;
  color: inherit;
  font-size: inherit;
}
.markdown-body strong {
  font-weight: 600;
  color: #1d2129;
}
.markdown-body table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 16px;
  font-size: 13px;
}
.markdown-body th, .markdown-body td {
  border: 1px solid #e5e6eb;
  padding: 8px 12px;
  text-align: left;
}
.markdown-body th {
  background: #f5f7fa;
  font-weight: 600;
}
.markdown-body hr {
  border: none;
  border-top: 1px solid #e5e6eb;
  margin: 16px 0;
}
.markdown-body a {
  color: #409eff;
}

/* ── Input Area ───────────────────────────────────────────────────── */
.chat-input :deep(.el-textarea__inner) {
  resize: none;
}
.chat-input-area {
  border-top: 1px solid #e4e7ed;
  background: #fff;
}
.model-bar {
  padding: 8px 20px 0;
}
.chat-input {
  display: flex;
  gap: 8px;
  padding: 8px 20px 16px;
}

/* ── Model selector responsive ────────────────────────────────────── */
.model-bar :deep(.el-select) {
  max-width: 220px;
  width: 100%;
}

/* ── Citation Markers ────────────────────────────────────────────────── */
.cite-marker {
  display: inline-block;
  color: #409eff;
  cursor: pointer;
  font-weight: 600;
  font-size: 0.85em;
  vertical-align: super;
  padding: 0 2px;
  transition: color .2s;
}
.cite-marker:hover {
  color: #337ecc;
  text-decoration: underline;
}

/* ── Citation Popup ──────────────────────────────────────────────────── */
.cite-popup-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(0,0,0,.15);
}
.cite-popup {
  position: fixed;
  transform: translate(-50%, 12px);
  width: min(420px, 90vw);
  max-height: 70vh;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0,0,0,.15);
  overflow-y: auto;
  z-index: 3001;
}
.cite-popup-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e6eb;
  font-weight: 600;
  font-size: 14px;
}
.cite-popup-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: #909399;
}
.cite-popup-body {
  padding: 12px 16px 16px;
}
.cite-source-title {
  font-weight: 600;
  font-size: 14px;
  color: #1d2129;
  margin-bottom: 4px;
}
.cite-abstract {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f2f3f5;
}
</style>
