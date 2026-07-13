<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'
import { useAuthStore } from '@/stores/auth'
import { onMounted, ref } from 'vue'
import { UserFilled, PriceTag, Plus } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import * as convApi from '@/api/conversations'

const router = useRouter()
const route = useRoute()
const convStore = useConversationStore()
const authStore = useAuthStore()

// ── Tag management state ──────────────────────────────────────
const tagEditorConvId = ref<string | null>(null)
const tagInput = ref('')
const tagEditTags = ref<string[]>([])
const tagLoading = ref(false)

async function openTagEditor(convId: string, tags: string[], event: Event) {
  event.stopPropagation()
  if (tagEditorConvId.value === convId) {
    tagEditorConvId.value = null
  } else {
    tagEditorConvId.value = convId
    tagEditTags.value = [...tags]
  }
}

async function handleAddTag(convId: string) {
  const t = tagInput.value.trim()
  if (!t) return
  tagLoading.value = true
  try {
    tagEditTags.value = await convApi.addTag(convId, t)
    // update the store's conversation object so it renders immediately
    const conv = convStore.conversations.find(c => c.id === convId)
    if (conv) conv.tags = [...tagEditTags.value]
    tagInput.value = ''
  } catch { /* ignore */ }
  tagLoading.value = false
}

async function handleRemoveTag(convId: string, tag: string) {
  try {
    tagEditTags.value = await convApi.removeTag(convId, tag)
    const conv = convStore.conversations.find(c => c.id === convId)
    if (conv) conv.tags = [...tagEditTags.value]
  } catch { /* ignore */ }
}

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

function goToDashboard() {
  router.push('/dashboard')
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
            <!-- inline tags -->
            <span class="conv-tags" v-if="conv.tags?.length">
              <el-tag
                v-for="t in conv.tags"
                :key="t"
                size="small"
                type="info"
                class="conv-tag-chip"
              >{{ t }}</el-tag>
            </span>
            <span class="conv-tag-btn" @click="openTagEditor(conv.id, conv.tags || [], $event)" title="管理标签">
              <el-icon :size="14"><PriceTag /></el-icon>
            </span>
            <span>{{ formatDate(conv.updatedAt) }}</span>
          </div>
          <!-- inline tag editor -->
          <div v-if="tagEditorConvId === conv.id" class="tag-editor" @click.stop>
            <div class="tag-editor-tags" v-if="tagEditTags.length">
              <el-tag
                v-for="t in tagEditTags"
                :key="t"
                size="small"
                closable
                @close="handleRemoveTag(conv.id, t)"
              >{{ t }}</el-tag>
            </div>
            <div class="tag-editor-input">
              <el-input
                v-model="tagInput"
                size="small"
                placeholder="输入标签..."
                @keyup.enter="handleAddTag(conv.id)"
              >
                <template #append>
                  <el-button :icon="Plus" size="small" :loading="tagLoading" @click="handleAddTag(conv.id)" />
                </template>
              </el-input>
            </div>
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
              <el-dropdown-item @click="goToDashboard">
                仪表盘
              </el-dropdown-item>
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

/* ── Tags ────────────────────────────────────────────────────── */

.conv-tags {
  display: flex;
  gap: 2px;
  flex-wrap: wrap;
}

.conv-tag-chip {
  font-size: 10px;
  height: 18px;
  line-height: 18px;
  padding: 0 5px;
}

.conv-tag-btn {
  cursor: pointer;
  color: #c0c4cc;
  display: flex;
  align-items: center;
}

.conv-tag-btn:hover {
  color: #409eff;
}

.tag-editor {
  margin-top: 6px;
  padding: 8px;
  background: #f5f7fa;
  border-radius: 6px;
}

.tag-editor-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.tag-editor-input {
  width: 100%;
}
</style>
