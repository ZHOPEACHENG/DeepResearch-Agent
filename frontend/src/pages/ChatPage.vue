<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'
import { useAuthStore } from '@/stores/auth'
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
                v-if="msg.metadata?.status === 'pending_confirmation'"
              >
                <el-button type="primary" size="small" @click="store.actOnPlan(msg.id, 'accept')">接受</el-button>
                <el-button size="small" @click="store.actOnPlan(msg.id, 'modify')">修改</el-button>
                <el-button size="small" type="danger" @click="handleRejectPlan(msg.id)">拒绝</el-button>
              </div>
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
                v-if="msg.metadata?.status === 'pending_confirmation'"
              >
                <el-button type="primary" size="small" @click="store.actOnPlan(msg.id, 'accept')">接受</el-button>
                <el-button size="small" @click="store.actOnPlan(msg.id, 'modify')">修改</el-button>
                <el-button size="small" type="danger" @click="handleRejectPlan(msg.id)">拒绝</el-button>
              </div>
            </div>
            <!-- Report card -->
            <div class="msg-card report" v-else-if="msg.messageType === 'report_card'">
              <p>研究报告已生成</p>
            </div>
          </div>
        </div>
      </template>

      <!-- Thinking indicator: shown when waiting for first token -->
      <div v-if="store.isStreaming && !store.streamingContent" class="message-row assistant">
        <el-avatar :size="34" class="msg-avatar ai-avatar">
          <el-icon :size="18"><Cpu /></el-icon>
        </el-avatar>
        <div class="msg-bubble thinking">
          <div class="thinking-dots">
            <span></span><span></span><span></span>
          </div>
          <span class="thinking-text">正在思考...</span>
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
