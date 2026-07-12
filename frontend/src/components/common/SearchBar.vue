<script setup lang="ts">
/**
 * SearchBar — keyword input for knowledge base search.
 *
 * Phase 7 / US5 (T109).
 *
 * Emits:
 *   @search(query: string) — when user submits a search
 */

import { ref } from 'vue'

const emit = defineEmits<{
  search: [query: string]
}>()

defineProps<{
  loading?: boolean
  placeholder?: string
}>()

const query = ref('')

function handleSearch() {
  const trimmed = query.value.trim()
  if (!trimmed) return
  emit('search', trimmed)
}

function handleClear() {
  query.value = ''
  emit('search', '')
}
</script>

<template>
  <div class="search-bar">
    <el-input
      v-model="query"
      :placeholder="placeholder || '搜索知识库...'"
      clearable
      @clear="handleClear"
      @keyup.enter="handleSearch"
    >
      <template #append>
        <el-button
          :loading="loading"
          :disabled="!query.trim()"
          @click="handleSearch"
        >
          搜索
        </el-button>
      </template>
    </el-input>
  </div>
</template>

<style scoped>
.search-bar {
  width: 100%;
}
</style>
