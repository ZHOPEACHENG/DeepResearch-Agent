<script setup lang="ts">
/**
 * FileUpload — drag-and-drop zone for knowledge base document upload.
 *
 * Phase 7 / US5 (T108).
 *
 * Emits:
 *   @upload(file: File) — when a valid file is selected
 *
 * Validates: file type (pdf/docx/txt/md), size (≤50 MB).
 * Shows drag-over visual feedback, error messages, and upload progress.
 */

import { ref, computed } from 'vue'
import { VALID_FILE_TYPES, MAX_FILE_SIZE_BYTES } from '@/types/document'

const emit = defineEmits<{
  upload: [file: File]
}>()

const isDragging = ref(false)
const validationError = ref<string | null>(null)
const uploading = ref(false)

const acceptedExtensions = computed(() => VALID_FILE_TYPES.join(','))

const sizeLimitText = computed(() => {
  const mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
  return `${mb} MB`
})

function handleDragOver(e: DragEvent) {
  e.preventDefault()
  isDragging.value = true
}

function handleDragLeave() {
  isDragging.value = false
}

function handleDrop(e: DragEvent) {
  e.preventDefault()
  isDragging.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    validateAndUpload(files[0])
  }
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement
  const files = target.files
  if (files && files.length > 0) {
    validateAndUpload(files[0])
  }
  // Reset so the same file can be re-selected
  target.value = ''
}

function validateAndUpload(file: File) {
  validationError.value = null

  // Check extension
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!VALID_FILE_TYPES.includes(ext)) {
    validationError.value = `不支持的文件格式 "${ext}"。支持: ${VALID_FILE_TYPES.join(', ')}`
    return
  }

  // Check size
  if (file.size > MAX_FILE_SIZE_BYTES) {
    const mb = (file.size / (1024 * 1024)).toFixed(1)
    validationError.value = `文件过大 (${mb} MB)。最大允许: ${sizeLimitText.value}`
    return
  }

  if (file.size === 0) {
    validationError.value = '文件为空，无法上传'
    return
  }

  emit('upload', file)
}

/** Called by parent to show uploading state */
function setUploading(val: boolean) {
  uploading.value = val
}

defineExpose({ setUploading })
</script>

<template>
  <div class="file-upload-wrapper">
    <div
      class="drop-zone"
      :class="{ dragging: isDragging, uploading }"
      @dragover="handleDragOver"
      @dragleave="handleDragLeave"
      @drop="handleDrop"
    >
      <input
        type="file"
        class="file-input"
        :accept="acceptedExtensions"
        @change="handleFileSelect"
        :disabled="uploading"
      />

      <div class="drop-content">
        <el-icon :size="40" class="upload-icon">
          <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/>
            <line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
        </el-icon>
        <p class="drop-title">
          {{ uploading ? '正在上传...' : '拖拽文件到此处或点击上传' }}
        </p>
        <p class="drop-hint">
          支持 PDF、DOCX、TXT、Markdown 格式，单文件最大 {{ sizeLimitText }}
        </p>
      </div>
    </div>

    <p v-if="validationError" class="validation-error" role="alert">
      {{ validationError }}
    </p>
  </div>
</template>

<style scoped>
.file-upload-wrapper {
  width: 100%;
}

.drop-zone {
  position: relative;
  border: 2px dashed #dcdfe6;
  border-radius: 8px;
  padding: 40px 20px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
  background: #fafafa;
}

.drop-zone:hover,
.drop-zone.dragging {
  border-color: #409eff;
  background: #ecf5ff;
}

.drop-zone.uploading {
  cursor: not-allowed;
  opacity: 0.7;
}

.file-input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.file-input:disabled {
  cursor: not-allowed;
}

.drop-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.upload-icon {
  color: #c0c4cc;
}

.drop-zone.dragging .upload-icon,
.drop-zone:hover .upload-icon {
  color: #409eff;
}

.drop-title {
  font-size: 15px;
  color: #606266;
  margin: 0;
}

.drop-hint {
  font-size: 12px;
  color: #c0c4cc;
  margin: 0;
}

.validation-error {
  color: #f56c6c;
  font-size: 13px;
  margin: 8px 0 0 0;
}
</style>
