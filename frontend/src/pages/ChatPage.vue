<script setup lang="ts">
import { ref, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'

const route = useRoute()
const router = useRouter()
const store = useConversationStore()

const inputText = ref('')
const chatContainer = ref<HTMLElement | null>(null)

const convId = ref<string | null>(null)

onMounted(async () => {
  await store.fetchAvailableModels()
  const id = route.params.conversationId as string | undefined
  if (id) {
    convId.value = id
    await store.fetchConversation(id)
  }
})

watch(() => route.params.conversationId, async (newId) => {
  const id = newId as string | undefined
  if (id && id !== convId.value) {
    convId.value = id
    await store.fetchConversation(id)
  } else if (!id) {
    convId.value = null
    store.currentConversation = null
    store.messages = []
  }
})

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

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
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

watch(() => store.streamingContent, scrollToBottom)
watch(() => store.messages.length, scrollToBottom)
</script>

<template>
  <div class="chat-page">
    <div class="chat-messages" ref="chatContainer">
      <div v-if="!convId && !store.messages.length" class="welcome">
        <h2>深度研究平台</h2>
        <p>输入研究主题或任意问题开始对话</p>
      </div>

      <div
        v-for="msg in store.messages"
        :key="msg.id"
        :class="['message', msg.role]"
      >
        <div class="msg-content" v-if="msg.messageType === 'text'">
          {{ msg.content }}
        </div>
        <div class="msg-card plan" v-else-if="msg.messageType === 'plan_card'">
          <h4>研究计划</h4>
          <ul v-if="(msg.metadata as any).questions?.length">
            <li v-for="(q, i) in (msg.metadata as any).questions" :key="i">{{ q }}</li>
          </ul>
          <p v-if="(msg.metadata as any).keywords?.length">
            <el-tag v-for="kw in (msg.metadata as any).keywords" :key="kw" size="small" style="margin-right:4px">{{ kw }}</el-tag>
          </p>
          <pre v-if="msg.content && msg.content !== 'Research plan generated'" style="white-space:pre-wrap;opacity:0.7">{{ msg.content }}</pre>
          <div class="plan-actions" v-if="(msg.metadata as any).status === 'pending_confirmation'">
            <el-button type="primary" size="small" @click="store.actOnPlan(msg.id, 'accept')">接受</el-button>
            <el-button size="small" @click="store.actOnPlan(msg.id, 'modify')">修改</el-button>
            <el-button size="small" type="danger" @click="store.actOnPlan(msg.id, 'reject')">拒绝</el-button>
          </div>
        </div>
        <div class="msg-card report" v-else-if="msg.messageType === 'report_card'">
          <p>研究报告已生成</p>
        </div>
      </div>

      <div v-if="store.streamingContent" class="message assistant streaming">
        {{ store.streamingContent }}
        <span class="cursor">|</span>
      </div>

      <div v-if="store.error" class="message error">
        {{ store.error }}
        <el-button size="small" @click="store.clearError()">关闭</el-button>
      </div>
    </div>

    <div class="chat-input-area">
      <div class="model-bar">
        <el-select
          v-model="store.selectedModel"
          size="small"
          :disabled="store.isStreaming"
          style="width: 220px"
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
          :rows="2"
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
          :disabled="store.isStreaming"
          @keydown="handleKeydown"
        />
        <el-button
          type="primary"
          :disabled="!inputText.trim()"
          :loading="store.isStreaming"
          @click="handleSend"
        >
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 800px;
  margin: 0 auto;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.welcome {
  text-align: center;
  margin-top: 30vh;
  color: #909399;
}
.welcome h2 { font-size: 24px; margin-bottom: 8px; }

.message {
  margin-bottom: 16px;
  padding: 12px 16px;
  border-radius: 12px;
  max-width: 80%;
}
.message.user { background: #ecf5ff; margin-left: auto; }
.message.assistant { background: #f5f7fa; }
.message.error { background: #fef0f0; color: #f56c6c; }

.msg-card {
  margin-bottom: 16px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 12px;
  border: 1px solid #e4e7ed;
}
.plan-actions { margin-top: 12px; display: flex; gap: 8px; }

.streaming .cursor { animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }

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
</style>
