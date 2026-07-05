<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'
import { useAuthStore } from '@/stores/auth'
import * as convApi from '@/api/conversations'
import { ElMessageBox } from 'element-plus'
import { Cpu, Loading } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const store = useConversationStore()
const authStore = useAuthStore()

const inputText = ref('')
const chatContainer = ref<HTMLElement | null>(null)

const convId = ref<string | null>(null)

onMounted(async () => {
  await store.fetchAvailableModels()
  const id = route.params.conversationId as string | undefined
  if (id) {
    convId.value = id
    await store.fetchConversation(id)
  } else {
    store.clearCurrentConversation()
  }
})

// Cleanup SSE stream on navigation away
onBeforeUnmount(() => {
  store.stopStreaming()
})

watch(() => route.params.conversationId, async (newId) => {
  const id = newId as string | undefined
  if (id && id !== convId.value) {
    convId.value = id
    await store.fetchConversation(id)
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
  store.actOnPlan(messageId, 'reject')
}

// ── Gap question action ────────────────────────────────────────────────
// 用户选择补充检索或跳过知识缺口。后端 resume 图后，后续 retrieval/report
// 卡片会先写入 DB，前端刷新对话即可看到（当前阶段靠手动/切换触发拉取）。
async function handleGapAction(msg: { id: string; metadata?: { taskId?: string } }, action: 'answer' | 'skip') {
  const taskId = msg.metadata?.taskId
  if (!taskId || !convId.value) return
  const target = store.messages.find(m => m.id === msg.id)
  if (target) target.metadata = { ...target.metadata, status: action === 'answer' ? 'answered' : 'skipped' }
  try {
    await convApi.actOnGap(taskId, action, convId.value)
  } catch (e) {
    console.error('[ChatPage] gap action failed:', e)
    if (target) target.metadata = { ...target.metadata, status: 'pending' }
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
    // Keep dialog open on error so the user can retry without losing text.
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

      <!-- Loading state -->
      <div v-if="store.loading" class="loading-state">
        <el-icon class="is-loading" :size="24"><Loading /></el-icon>
        <p>加载对话中...</p>
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
                <el-button type="primary" size="small" @click="store.actOnPlan(msg.id, 'accept')">接受</el-button>
                <el-button size="small" @click="openModifyDialog(msg.id)">修改</el-button>
                <el-button size="small" type="danger" @click="handleRejectPlan(msg.id)">拒绝</el-button>
              </div>
              <el-tag
                v-else-if="msg.metadata?.status === 'accepted_with_notes'"
                type="success" size="small" style="margin-top:8px"
              >已采纳补充：{{ msg.metadata?.user_focus_notes || '' }}</el-tag>
            </div>
            <!-- Report card -->
            <div class="msg-card report" v-else-if="msg.messageType === 'report_card'">
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
            <div class="msg-bubble ai-bubble" v-if="msg.messageType === 'text'">
              {{ msg.content }}
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
                <el-button type="primary" size="small" @click="store.actOnPlan(msg.id, 'accept')">接受</el-button>
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
            <!-- Gap question card -->
            <div
              class="msg-card gap"
              v-else-if="msg.messageType === 'gap_question'"
            >
              <h4>知识缺口 · 第 {{ msg.metadata?.round || 1 }} 轮</h4>
              <p class="gap-hint">以下问题暂未找到充分资料，是否补充检索？</p>
              <ul v-if="Array.isArray(msg.metadata?.gaps)" class="gap-list">
                <li
                  v-for="(g, gidx) in (msg.metadata?.gaps as any[])"
                  :key="gidx"
                >
                  <el-tag
                    size="small"
                    :type="g.severity === 'critical' ? 'danger' : 'warning'"
                    style="margin-right:6px"
                  >{{ g.severity }}</el-tag>
                  <span>{{ g.description }}</span>
                </li>
              </ul>
              <div
                class="gap-actions"
                v-if="msg.metadata?.status === 'pending'"
              >
                <el-button
                  type="primary" size="small"
                  :disabled="store.isStreaming"
                  @click="handleGapAction(msg, 'answer')"
                >补充检索</el-button>
                <el-button
                  size="small"
                  :disabled="store.isStreaming"
                  @click="handleGapAction(msg, 'skip')"
                >跳过</el-button>
              </div>
              <el-tag
                v-else-if="msg.metadata?.status === 'answered'"
                type="success" size="small" style="margin-top:8px"
              >已补充检索</el-tag>
                <el-tag
                  v-else-if="msg.metadata?.status === 'skipped'"
                  type="info" size="small" style="margin-top:8px"
                >已跳过</el-tag>
            </div>
            <!-- Report card -->
            <div class="msg-card report" v-else-if="msg.messageType === 'report_card'">
              <h4>{{ msg.metadata?.title || '研究报告' }}</h4>
              <p v-if="msg.metadata?.abstract" class="report-abstract">
                {{ msg.metadata?.abstract }}
              </p>
              <el-collapse
                v-if="Array.isArray(msg.metadata?.sections) && (msg.metadata?.sections as any[]).length"
              >
                <el-collapse-item
                  v-for="(section, sidx) in (msg.metadata?.sections as any[])"
                  :key="sidx"
                  :title="section.heading"
                  :name="`${msg.id}-${sidx}`"
                >
                  <div class="report-section-content">{{ section.content }}</div>
                </el-collapse-item>
              </el-collapse>
              <div
                v-if="msg.metadata?.gap_notes"
                class="report-gap-notes"
              >
                <h5>知识缺口说明</h5>
                <pre style="white-space:pre-wrap">{{ msg.metadata?.gap_notes }}</pre>
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
                    <span>{{ cite.text }}</span>
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
          v-model="store.deepThinking"
          size="small"
          :disabled="store.isStreaming"
          inline-prompt
          active-text="深度思考"
          inactive-text="深度思考"
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
  text-align: center;
  margin-top: 30vh;
  color: #909399;
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
  display: contents; /* transparent to flex — bubble becomes direct flex child */
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
.report-abstract {
  color: #606266;
  font-size: 13px;
  line-height: 1.6;
  margin: 0 0 8px;
}
.report-section-content {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.7;
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
</style>
