<script setup lang="ts">
/**
 * DashboardPage — overview of the user's research activity.
 *
 * T120: Shows summary cards (conversations, active research, completed,
 * knowledge docs) and a recent conversations quick-access list.
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'
import { useKnowledgeStore } from '@/stores/knowledge'
import {
  ChatDotRound, Document, Files, Plus,
} from '@element-plus/icons-vue'

const router = useRouter()
const convStore = useConversationStore()
const knowledgeStore = useKnowledgeStore()

interface DashboardStats {
  totalConversations: number
  completedResearchCount: number
  knowledgeDocCount: number
}

const stats = ref<DashboardStats>({
  totalConversations: 0,
  completedResearchCount: 0,
  knowledgeDocCount: 0,
})
const loading = ref(true)

onMounted(async () => {
  loading.value = true
  try {
    const [convData] = await Promise.all([
      convStore.fetchConversations(),
      knowledgeStore.fetchDocuments(),
    ])
    // Fetch dashboard stats from the new endpoint
    const token = localStorage.getItem('access_token')
    const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'
    const resp = await fetch(`${baseUrl}/dashboard/stats`, {
      headers: { Authorization: token ? `Bearer ${token}` : '' },
    })
    if (resp.ok) {
      stats.value = await resp.json()
    }
  } catch {
    // Fall back to computed stats from loaded data
    stats.value.totalConversations = convStore.conversations.length
    stats.value.knowledgeDocCount = knowledgeStore.documents.length
  } finally {
    loading.value = false
  }
})

function goToChat() {
  router.push('/chat')
}

function goToKnowledge() {
  router.push('/knowledge')
}

function goToConversation(id: string) {
  router.push(`/chat/${id}`)
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-CN')
}
</script>

<template>
  <div class="dashboard">
    <div class="dashboard-header">
      <h1>仪表盘</h1>
      <el-button type="primary" :icon="Plus" @click="goToChat">
        新建对话
      </el-button>
    </div>

    <!-- Overview Cards -->
    <div class="stats-grid">
      <el-card class="stat-card" shadow="hover" v-loading="loading">
        <div class="stat-icon chats"><el-icon :size="28"><ChatDotRound /></el-icon></div>
        <div class="stat-body">
          <div class="stat-value">{{ stats.totalConversations }}</div>
          <div class="stat-label">总对话数</div>
        </div>
      </el-card>

      <el-card class="stat-card" shadow="hover" v-loading="loading">
        <div class="stat-icon completed"><el-icon :size="28"><Files /></el-icon></div>
        <div class="stat-body">
          <div class="stat-value">{{ stats.completedResearchCount }}</div>
          <div class="stat-label">已生成报告</div>
        </div>
      </el-card>

      <el-card class="stat-card" shadow="hover" v-loading="loading">
        <div class="stat-icon docs"><el-icon :size="28"><Document /></el-icon></div>
        <div class="stat-body">
          <div class="stat-value">{{ stats.knowledgeDocCount }}</div>
          <div class="stat-label">知识库文档</div>
        </div>
      </el-card>
    </div>

    <!-- Recent Conversations -->
    <el-card class="recent-card" shadow="hover">
      <template #header>
        <div class="recent-header">
          <span>最近对话</span>
        </div>
      </template>

      <div v-if="!convStore.conversations.length && !convStore.loading" class="empty-state">
        <p>暂无对话</p>
        <el-button type="primary" @click="goToChat">开始第一个对话</el-button>
      </div>

      <el-table
        v-else
        :data="convStore.conversations.slice(0, 5)"
        style="width: 100%"
        @row-click="({ id }) => goToConversation(id)"
        row-class-name="clickable-row"
        v-loading="convStore.loading"
      >
        <el-table-column prop="title" label="标题" min-width="200">
          <template #default="{ row }">
            <div class="conv-title-col">
              <span class="conv-name">{{ row.title }}</span>
              <span class="conv-model-tag">{{ row.model }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="messageCount" label="消息数" width="100" />
        <el-table-column label="标签" width="180">
          <template #default="{ row }">
            <el-tag
              v-for="t in (row.tags || []).slice(0, 3)"
              :key="t"
              size="small"
              type="info"
              style="margin-right: 4px"
            >{{ t }}</el-tag>
            <el-tag v-if="(row.tags || []).length > 3" size="small" type="info">
              +{{ row.tags.length - 3 }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="120">
          <template #default="{ row }">
            {{ formatDate(row.updatedAt) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.dashboard {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.dashboard-header h1 {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

/* ── Stats Grid ──────────────────────────────────────────────────── */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  :deep(.el-card__body) {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 20px;
  }
}

.stat-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-icon.chats { background: #ecf5ff; color: #409eff; }
.stat-icon.completed { background: #f0f9eb; color: #67c23a; }
.stat-icon.docs { background: #fef0f0; color: #f56c6c; }

.stat-body {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
  line-height: 1.2;
}

.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 2px;
}

/* ── Recent Conversations ────────────────────────────────────────── */
.recent-card {
  margin-top: 0;
}

.recent-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.empty-state {
  text-align: center;
  padding: 40px 0;
  color: #909399;
}

.conv-title-col {
  display: flex;
  align-items: center;
  gap: 8px;
}

.conv-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-model-tag {
  font-size: 11px;
  color: #909399;
  background: #f0f0f0;
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}

:deep(.clickable-row) {
  cursor: pointer;
}

:deep(.clickable-row:hover > td) {
  background: #f5f7fa;
}

/* ── Responsive ──────────────────────────────────────────────────── */
@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
