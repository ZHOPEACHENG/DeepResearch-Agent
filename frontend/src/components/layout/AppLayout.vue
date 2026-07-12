<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'
import { useAuthStore } from '@/stores/auth'
import { onMounted } from 'vue'
import { UserFilled } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'

const router = useRouter()
const route = useRoute()
const convStore = useConversationStore()
const authStore = useAuthStore()

onMounted(() => {
  convStore.fetchConversations()
  // authStore.user is already loaded by main.ts init() before mount;
  // fetchProfile() here is only needed if init() failed silently.
  // Since init() runs before mount, user should be populated.
  if (!authStore.isAuthenticated) {
    authStore.fetchProfile()
  }
})

function goToChat(id?: string) {
  router.push(id ? `/chat/${id}` : '/chat')
}

function goToKnowledge() {
  router.push('/knowledge')
}

function goToProfile() {
  router.push('/profile')
}

async function handleLogout() {
  await authStore.logoutUser()
  // Clear all conversation state to prevent data leakage to next user
  convStore.clearCurrentConversation()
  convStore.conversations = []
  convStore.error = null
  router.push('/login')
}

async function handleDeleteConversation(convId: string, event: Event) {
  event.stopPropagation()
  try {
    await ElMessageBox.confirm(
      '确定要删除此对话吗？此操作不可撤销。',
      '确认删除',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  await convStore.deleteConversation(convId)
  if (route.params.conversationId === convId) {
    router.push('/chat')
  }
}

function onConvKeydown(e: KeyboardEvent, convId: string) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    goToChat(convId)
  }
}

// Memoized date formatting to avoid repeated Date + toLocaleDateString calls
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-CN')
}
</script>

<template>
  <div class="app-layout">
    <aside class="sidebar" role="navigation" aria-label="对话列表">
      <el-button type="primary" class="new-chat-btn" @click="goToChat()">
        + 新建对话
      </el-button>

      <div class="conv-list" role="list">
        <!-- Empty state -->
        <div v-if="!convStore.conversations.length && !convStore.loading" class="empty-conv">
          <p>暂无对话</p>
          <p class="hint">点击上方按钮开始新的研究对话</p>
        </div>

        <div
          v-for="conv in convStore.conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: route.params.conversationId === conv.id }"
          role="button"
          :tabindex="0"
          :aria-label="`对话: ${conv.title}`"
          @click="goToChat(conv.id)"
          @keydown="onConvKeydown($event, conv.id)"
          @contextmenu.prevent="handleDeleteConversation(conv.id, $event)"
        >
          <div class="conv-header">
            <span class="conv-title">{{ conv.title }}</span>
            <span class="conv-model">{{ conv.model }}</span>
          </div>
          <div class="conv-preview" v-if="conv.lastMessagePreview">
            {{ conv.lastMessagePreview }}
          </div>
          <div class="conv-meta">
            <span>{{ conv.messageCount }}条</span>
            <span>{{ formatDate(conv.updatedAt) }}</span>
          </div>
        </div>
      </div>

      <!-- User menu footer -->
      <div class="sidebar-footer">
        <el-dropdown trigger="click" placement="top-start">
          <div class="user-info">
            <el-avatar :size="32">
              <el-icon :size="18"><UserFilled /></el-icon>
            </el-avatar>
            <span class="user-name">{{ authStore.userName }}</span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="goToKnowledge">
                知识库管理
              </el-dropdown-item>
              <el-dropdown-item @click="goToProfile">
                个人资料
              </el-dropdown-item>
              <el-dropdown-item divided @click="handleLogout">
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </aside>

    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100dvh;
}

.sidebar {
  width: 280px;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  background: #fafafa;
}

.new-chat-btn {
  margin: 12px;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
}

.empty-conv {
  text-align: center;
  padding: 32px 16px;
  color: #909399;
  font-size: 14px;
}
.empty-conv .hint {
  font-size: 12px;
  margin-top: 4px;
  color: #c0c4cc;
}

.conv-item {
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid #f0f0f0;
  outline: none;
}
.conv-item:focus-visible {
  box-shadow: inset 0 0 0 2px #409eff;
}

.conv-item:hover,
.conv-item.active {
  background: #ecf5ff;
}

.conv-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.conv-title {
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.conv-model {
  font-size: 11px;
  color: #909399;
  background: #f0f0f0;
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}

.conv-preview {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 4px;
}

/* ── Sidebar Footer / User Menu ──────────────────────────────────── */

.sidebar-footer {
  border-top: 1px solid #e4e7ed;
  padding: 10px 12px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px;
  border-radius: 8px;
  transition: background 0.15s;
}

.user-info:hover {
  background: #ecf5ff;
}

.user-name {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content {
  flex: 1;
  overflow-y: auto;
}
</style>
