<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useConversationStore } from '@/stores/conversations'
import { onMounted } from 'vue'

const router = useRouter()
const store = useConversationStore()

onMounted(() => store.fetchConversations())

function goToChat(id?: string) {
  router.push(id ? `/chat/${id}` : '/chat')
}
</script>

<template>
  <div class="app-layout">
    <aside class="sidebar">
      <el-button type="primary" class="new-chat-btn" @click="goToChat()">
        + 新建对话
      </el-button>
      <div class="conv-list">
        <div
          v-for="conv in store.conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: $route.params.conversationId === conv.id }"
          @click="goToChat(conv.id)"
        >
          <span class="conv-title">{{ conv.title }}</span>
          <span class="conv-date">{{ new Date(conv.updatedAt).toLocaleDateString('zh-CN') }}</span>
        </div>
      </div>
    </aside>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-layout { display: flex; height: 100vh; }
.sidebar {
  width: 280px; border-right: 1px solid #e4e7ed; display: flex;
  flex-direction: column; background: #fafafa;
}
.new-chat-btn { margin: 12px; }
.conv-list { flex: 1; overflow-y: auto; }
.conv-item {
  padding: 12px 16px; cursor: pointer; border-bottom: 1px solid #f0f0f0;
}
.conv-item:hover, .conv-item.active { background: #ecf5ff; }
.conv-title { display: block; font-size: 14px; font-weight: 500; }
.conv-date { font-size: 12px; color: #909399; }
.content { flex: 1; overflow: hidden; }
</style>
