<script setup lang="ts">
/**
 * KnowledgeBasePage — document upload, search, and QA.
 *
 * Phase 7 / US5 (T107 + T110).
 *
 * Three-panel layout:
 *   Left  — document list (upload, status, delete)
 *   Right — search bar + results + QA panel
 */

import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Document, Loading, QuestionFilled, Search } from '@element-plus/icons-vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import FileUpload from '@/components/common/FileUpload.vue'
import SearchBar from '@/components/common/SearchBar.vue'
import { renderMarkdown } from '@/utils/markdown'
import { fetchDocumentContent } from '@/api/knowledge'
import type { DocumentContent } from '@/api/knowledge'
import type { DocumentItem } from '@/types/document'

const store = useKnowledgeStore()

const fileUploadRef = ref<InstanceType<typeof FileUpload> | null>(null)
const askInput = ref('')
const activeTab = ref('search')

// ── Preview ─────────────────────────────────────────────────────────
const previewVisible = ref(false)
const previewLoading = ref(false)
const previewDoc = ref<DocumentContent | null>(null)

async function handlePreview(doc: DocumentItem) {
  previewVisible.value = true
  previewLoading.value = true
  previewDoc.value = null
  try {
    previewDoc.value = await fetchDocumentContent(doc.id)
  } catch {
    ElMessage.error('加载文档内容失败')
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}

// ── Lifecycle ─────────────────────────────────────────────────────
onMounted(() => {
  store.fetchDocuments()
})

// ── Upload ────────────────────────────────────────────────────────
async function handleUpload(file: File) {
  fileUploadRef.value?.setUploading(true)
  try {
    await store.uploadDocument(file)
    ElMessage.success(`"${file.name}" 上传成功，正在处理...`)
  } catch {
    // Error already set in store
  } finally {
    fileUploadRef.value?.setUploading(false)
  }
}

// ── Delete ────────────────────────────────────────────────────────
async function handleDelete(doc: DocumentItem) {
  try {
    await ElMessageBox.confirm(
      `确定要删除 "${doc.filename}" 吗？所有索引数据将被移除。`,
      '确认删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  try {
    await store.deleteDocument(doc.id)
    ElMessage.success(`"${doc.filename}" 已删除`)
  } catch {
    // Error already set in store
  }
}

// ── Search ────────────────────────────────────────────────────────
function handleSearch(query: string) {
  if (!query) {
    store.clearSearch()
    return
  }
  store.search(query)
}

// ── QA ────────────────────────────────────────────────────────────
async function handleAsk() {
  const q = askInput.value.trim()
  if (!q || store.askLoading) return
  await store.askQuestion(q)
}

// ── Status helpers ────────────────────────────────────────────────
const STATUS_MAP: Record<string, { label: string; type: '' | 'success' | 'warning' | 'info' | 'danger' }> = {
  pending:    { label: '等待中', type: 'info' },
  processing: { label: '处理中', type: 'warning' },
  completed:  { label: '已完成', type: 'success' },
  failed:     { label: '失败', type: 'danger' },
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-CN')
}

/** Count of active (pending/processing) documents */
const activeCount = computed(() =>
  store.documents.filter(d => d.processingStatus === 'pending' || d.processingStatus === 'processing').length,
)
</script>

<template>
  <div class="knowledge-page">
    <!-- ── Header ──────────────────────────────────────────────── -->
    <header class="page-header">
      <h2>个人知识库</h2>
      <span class="header-sub" v-if="store.total">
        共 {{ store.total }} 篇文档
        <template v-if="activeCount">（{{ activeCount }} 篇处理中）</template>
      </span>
    </header>

    <!-- ── Error ────────────────────────────────────────────────── -->
    <el-alert
      v-if="store.error"
      :title="store.error"
      type="error"
      show-icon
      closable
      class="page-error"
      @close="store.clearError()"
    />

    <!-- ── Body: two columns ────────────────────────────────────── -->
    <div class="kb-body">
      <!-- ── Left panel: upload + document list ────────────────── -->
      <aside class="left-panel">
        <FileUpload ref="fileUploadRef" @upload="handleUpload" />

        <!-- Document list -->
        <div class="doc-list">
          <!-- Loading skeletons (T121) -->
          <template v-if="store.loading">
            <div v-for="n in 5" :key="'skel-'+n" class="doc-item">
              <el-skeleton animated style="width:100%">
                <template #template>
                  <div style="display:flex;align-items:center;gap:10px;width:100%">
                    <el-skeleton-item variant="circle" style="width:20px;height:20px" />
                    <div style="flex:1">
                      <el-skeleton-item variant="text" style="width:70%;height:18px" />
                      <el-skeleton-item variant="text" style="width:50%;height:14px;margin-top:4px" />
                    </div>
                    <el-skeleton-item variant="text" style="width:50px;height:22px" />
                  </div>
                </template>
              </el-skeleton>
            </div>
          </template>

          <div v-if="!store.loading && !store.documents.length" class="empty-state">
            <el-icon :size="48"><Document /></el-icon>
            <p>暂无文档</p>
            <p class="hint">上传 PDF、DOCX、TXT 或 Markdown 文件开始构建知识库</p>
          </div>

          <div
            v-for="doc in store.documents"
            :key="doc.id"
            class="doc-item"
            :class="{ clickable: doc.processingStatus === 'completed' }"
            @click="doc.processingStatus === 'completed' && handlePreview(doc)"
          >
            <div class="doc-icon">
              <el-icon v-if="doc.processingStatus === 'processing'" class="is-loading" :size="20">
                <Loading />
              </el-icon>
              <el-icon v-else :size="20">
                <Document />
              </el-icon>
            </div>
            <div class="doc-info">
              <div class="doc-name">{{ doc.filename }}</div>
              <div class="doc-meta">
                <span>{{ formatSize(doc.fileSizeBytes) }}</span>
                <span>·</span>
                <span>{{ doc.fileType.toUpperCase() }}</span>
                <span>·</span>
                <span>{{ formatDate(doc.createdAt) }}</span>
              </div>
              <div v-if="doc.processingError" class="doc-error">
                {{ doc.processingError }}
              </div>
            </div>
            <div class="doc-actions">
              <el-tag
                :type="STATUS_MAP[doc.processingStatus]?.type ?? 'info'"
                size="small"
                effect="plain"
              >
                {{ STATUS_MAP[doc.processingStatus]?.label ?? doc.processingStatus }}
              </el-tag>
              <el-button
                type="danger"
                :icon="Delete"
                circle
                size="small"
                text
                @click="handleDelete(doc)"
                title="删除文档"
              />
            </div>
          </div>
        </div>
      </aside>

      <!-- ── Right panel: search & QA with tabs ────────────────── -->
      <section class="right-panel">
        <el-tabs v-model="activeTab" class="kb-tabs">
          <!-- ═══ Search Tab ═══ -->
          <el-tab-pane name="search">
            <template #label>
              <span class="tab-label">
                <el-icon :size="16"><Search /></el-icon> 搜索
              </span>
            </template>

            <SearchBar
              :loading="store.searchLoading"
              placeholder="混合检索：关键词 + 语义向量搜索..."
              @search="handleSearch"
            />
            <div v-if="store.searchTotal > 0" class="search-meta">
              找到 {{ store.searchTotal }} 条结果
            </div>
            <div v-if="store.searchQuery && !store.searchLoading && store.searchTotal === 0" class="search-empty">
              <p>未找到与 "{{ store.searchQuery }}" 相关的内容</p>
              <p class="hint">试试用不同的关键词，或先上传一些文档到知识库</p>
            </div>

            <div v-if="store.searchResults.length" class="search-results">
              <div v-for="(item, idx) in store.searchResults" :key="idx" class="result-item">
                <div class="result-header">
                  <strong>{{ item.filename }}</strong>
                  <el-tag size="small" type="info" effect="plain" v-if="item.chunkIndex !== undefined">
                    片段 #{{ item.chunkIndex }}
                  </el-tag>
                </div>
                <p class="result-text">{{ item.text.slice(0, 400) }}{{ item.text.length > 400 ? '...' : '' }}</p>
              </div>
            </div>
          </el-tab-pane>

          <!-- ═══ QA Tab ═══ -->
          <el-tab-pane name="ask">
            <template #label>
              <span class="tab-label">
                <el-icon :size="16"><QuestionFilled /></el-icon> 智能问答
              </span>
            </template>

            <div class="qa-input-row">
              <el-input
                v-model="askInput"
                placeholder="基于知识库内容提问，支持自然语言..."
                :disabled="store.askLoading"
                clearable
                @keyup.enter="handleAsk"
              >
                <template #append>
                  <el-button
                    type="primary"
                    :loading="store.askLoading"
                    :disabled="!askInput.trim()"
                    @click="handleAsk"
                  >提问</el-button>
                </template>
              </el-input>
            </div>

            <div class="qa-hint">
              混合检索（关键词 + 语义）→ RRF 融合排序 → LLM 生成回答
            </div>

            <div v-if="store.askLoading" class="qa-loading">
              <el-icon class="is-loading" :size="24"><Loading /></el-icon>
              <span>正在检索知识库并生成回答...</span>
            </div>

            <div v-if="store.lastAnswer && !store.askLoading" class="qa-answer">
              <div class="qa-question-label">Q: {{ store.lastQuestion }}</div>
              <div class="qa-answer-content markdown-body" v-html="renderMarkdown(store.lastAnswer)" />

              <div v-if="store.lastAnswerSources.length" class="qa-sources">
                <h4>参考来源（{{ store.lastAnswerSources.length }} 个文档片段）</h4>
                <div v-for="(src, idx) in store.lastAnswerSources" :key="idx" class="qa-source-item">
                  <div class="source-header">
                    <span class="source-index">[{{ idx + 1 }}]</span>
                    <span class="source-file">{{ src.filename }}</span>
                    <span v-if="src.pageNumber" class="source-page">· 第{{ src.pageNumber }}页</span>
                  </div>
                  <div class="source-excerpt">{{ src.excerpt.slice(0, 300) }}{{ src.excerpt.length > 300 ? '...' : '' }}</div>
                </div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </section>
    </div>

    <!-- ── Document Preview Dialog ──────────────────────────────── -->
    <el-dialog
      v-model="previewVisible"
      :title="previewDoc?.filename || '文档预览'"
      width="720px"
      top="5vh"
      destroy-on-close
    >
      <div v-loading="previewLoading" class="preview-body">
        <template v-if="previewDoc?.chunks.length">
          <div
            v-for="chunk in previewDoc.chunks"
            :key="chunk.chunkIndex"
            class="preview-chunk"
          >
            <div class="preview-chunk-label">片段 {{ chunk.chunkIndex }}</div>
            <div class="preview-chunk-text">{{ chunk.text }}</div>
          </div>
        </template>
        <div v-else-if="!previewLoading" class="preview-empty">
          暂无内容
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.knowledge-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* ── Header ─────────────────────────────────────────────────────── */

.page-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.header-sub {
  font-size: 13px;
  color: #909399;
}

.page-error {
  margin-bottom: 16px;
}

/* ── Body ───────────────────────────────────────────────────────── */

.kb-body {
  flex: 1;
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 24px;
  overflow: hidden;
}

/* ── Left panel ─────────────────────────────────────────────────── */

.left-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

.doc-list {
  flex: 1;
  overflow-y: auto;
}

.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: #c0c4cc;
}

.empty-state p {
  margin: 8px 0 0;
  font-size: 14px;
}
.empty-state .hint {
  font-size: 12px;
  color: #dcdfe6;
}

.doc-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  margin-bottom: 8px;
  background: #fff;
  transition: border-color 0.15s;
}

.doc-item:hover {
  border-color: #c6e2ff;
}
.doc-item.clickable {
  cursor: pointer;
}

.doc-icon {
  color: #909399;
  padding-top: 2px;
  flex-shrink: 0;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 11px;
  color: #c0c4cc;
  margin-top: 2px;
  display: flex;
  gap: 6px;
}

.doc-error {
  font-size: 11px;
  color: #f56c6c;
  margin-top: 4px;
}

.doc-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* ── Right panel ────────────────────────────────────────────────── */

.right-panel {
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.search-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.search-meta {
  font-size: 12px;
  color: #909399;
}

.search-empty {
  font-size: 13px;
  color: #c0c4cc;
  text-align: center;
  padding: 20px;
}

.search-results {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 300px;
  overflow-y: auto;
}

.result-item {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px 12px;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  margin-bottom: 4px;
}

.result-score {
  font-size: 11px;
  color: #909399;
}

.result-highlight {
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.result-highlight :deep(em) {
  background: #fff3cd;
  font-style: normal;
  padding: 0 2px;
}

.result-text {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
  margin: 4px 0 0;
}

/* ── QA ─────────────────────────────────────────────────────────── */

.qa-section h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  margin: 0 0 12px;
  color: #303133;
}

.qa-input-row {
  margin-bottom: 16px;
}

.qa-answer {
  border: 1px solid #e6f7e6;
  border-radius: 8px;
  background: #f0fff4;
  padding: 16px;
}

.qa-question-label {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}

.qa-hint {
  font-size: 11px;
  color: #c0c4cc;
  text-align: center;
  margin-top: 6px;
}

.qa-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 0;
  color: #909399;
  font-size: 14px;
}

.qa-answer-content {
  font-size: 14px;
  line-height: 1.7;
  color: #303133;
}

/* markdown-body styling for rendered answer */
.qa-answer-content :deep(h1),
.qa-answer-content :deep(h2),
.qa-answer-content :deep(h3) {
  margin: 12px 0 6px;
}
.qa-answer-content :deep(p) { margin: 4px 0; }
.qa-answer-content :deep(ul), .qa-answer-content :deep(ol) { padding-left: 20px; margin: 4px 0; }
.qa-answer-content :deep(code) {
  background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 13px;
}
.qa-answer-content :deep(pre) {
  background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto;
}

.qa-sources {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #d4edda;
}

.qa-sources h4 {
  font-size: 13px;
  margin: 0 0 8px;
  color: #606266;
}

.qa-source-item {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
  padding: 8px;
  background: #fff;
  border: 1px solid #e8f5e9;
  border-radius: 4px;
}

.source-header {
  display: flex;
  gap: 6px;
  align-items: baseline;
  margin-bottom: 4px;
}

.source-index {
  font-weight: 600;
  color: #409eff;
}

.source-file {
  font-weight: 500;
  color: #606266;
}

.source-excerpt {
  color: #909399;
  line-height: 1.5;
}

.source-page {
  color: #c0c4cc;
}

/* ── Tabs ─────────────────────────────────────────────────────────── */

.kb-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.kb-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow-y: auto;
}

.tab-label {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* ── Responsive ─────────────────────────────────────────────────── */

@media (max-width: 768px) {
  .kb-body {
    grid-template-columns: 1fr;
  }

  .knowledge-page {
    padding: 12px;
  }
}

/* ── Preview Dialog ─────────────────────────────────────────────── */

.preview-body {
  max-height: 60vh;
  overflow-y: auto;
}

.preview-chunk {
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}

.preview-chunk:last-child {
  border-bottom: none;
}

.preview-chunk-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}

.preview-chunk-text {
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}

.preview-empty {
  text-align: center;
  padding: 40px;
  color: #c0c4cc;
  font-size: 14px;
}
</style>
