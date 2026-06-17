// MongoDB initialization script for DeepResearch platform.
// Runs on first container startup via docker-entrypoint-initdb.d.
// Creates collections with validators and indexes for research stage outputs.

const DB_NAME = "deepresearch";
const db = db.getSiblingDB(DB_NAME);

print(`[init.js] Initializing MongoDB database: ${DB_NAME}`);

// ── research_plans ─────────────────────────────────────────────────────

db.createCollection("research_plans", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["task_id", "topic", "questions"],
      properties: {
        task_id: { bsonType: "string", description: "UUID of the ResearchTask" },
        conversation_id: { bsonType: ["string", "null"], description: "UUID of the Conversation (null for non-chat tasks)" },
        topic: { bsonType: "string", description: "Original research topic" },
        questions: {
          bsonType: "array",
          description: "Hierarchical research question tree",
        },
        search_keywords: { bsonType: "array", description: "Generated search keywords" },
        priority_order: { bsonType: "array", description: "Question priority ordering" },
        generated_at: { bsonType: "date", description: "When the plan was generated" },
      },
    },
  },
  validationLevel: "moderate",
  validationAction: "warn",
});

db.research_plans.createIndex({ task_id: 1 }, { unique: true, name: "idx_plan_task_id" });

// ── retrieval_results ──────────────────────────────────────────────────

db.createCollection("retrieval_results", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["task_id", "title", "source_url"],
      properties: {
        task_id: { bsonType: "string", description: "UUID of the ResearchTask" },
        conversation_id: { bsonType: ["string", "null"], description: "UUID of the Conversation (null for non-chat tasks)" },
        round_number: { bsonType: "int", minimum: 1, description: "Which retrieval round (1-3)" },
        title: { bsonType: "string", description: "Result title" },
        abstract: { bsonType: "string", description: "Short abstract/summary" },
        excerpt: { bsonType: "string", description: "Relevant excerpt from the source" },
        source_url: { bsonType: "string", description: "URL or identifier of the source" },
        doi: { bsonType: ["string", "null"], description: "DOI if available" },
        source_type: {
          enum: ["web", "arxiv", "semantic_scholar", "knowledge_base", "other"],
          description: "Search source type",
        },
        authors: { bsonType: "array", description: "Author names" },
        publication_date: { bsonType: ["string", "null"], description: "Publication date string" },
        credibility: {
          enum: ["high", "medium", "low", "unknown"],
          description: "Source credibility assessment",
        },
        snapshot_text: { bsonType: "string", description: "Full text snapshot at retrieval time" },
        retrieved_at: { bsonType: "date", description: "When the result was retrieved" },
      },
    },
  },
  validationLevel: "moderate",
  validationAction: "warn",
});

db.retrieval_results.createIndex({ task_id: 1, round_number: 1 }, { name: "idx_retrieval_task_round" });
db.retrieval_results.createIndex({ source_url: 1 }, { name: "idx_retrieval_source_url" });
db.retrieval_results.createIndex({ credibility: 1 }, { name: "idx_retrieval_credibility" });

// ── knowledge_summaries ────────────────────────────────────────────────

db.createCollection("knowledge_summaries", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["task_id", "phase", "summary_content"],
      properties: {
        task_id: { bsonType: "string", description: "UUID of the ResearchTask" },
        conversation_id: { bsonType: ["string", "null"], description: "UUID of the Conversation (null for non-chat tasks)" },
        phase: {
          enum: ["initial", "gap_fill_1", "gap_fill_2", "gap_fill_3"],
          description: "Which analysis round produced this summary",
        },
        summary_content: { bsonType: "string", description: "The integrated knowledge summary text" },
        citation_map: {
          bsonType: "object",
          description: "knowledge_chunk_id → retrieval_result_id mapping",
        },
        generated_at: { bsonType: "date", description: "When the summary was generated" },
      },
    },
  },
  validationLevel: "moderate",
  validationAction: "warn",
});

db.knowledge_summaries.createIndex({ task_id: 1, phase: 1 }, { name: "idx_summary_task_phase" });

// ── knowledge_gaps ─────────────────────────────────────────────────────

db.createCollection("knowledge_gaps", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["task_id", "gap_id", "description"],
      properties: {
        task_id: { bsonType: "string", description: "UUID of the ResearchTask" },
        conversation_id: { bsonType: ["string", "null"], description: "UUID of the Conversation (null for non-chat tasks)" },
        gap_id: { bsonType: "string", description: "UUID of this gap entry" },
        description: { bsonType: "string", description: "Description of the knowledge gap" },
        related_question_id: { bsonType: "string", description: "Which research question this gap relates to" },
        severity: {
          enum: ["critical", "moderate", "minor"],
          description: "How severely this gap impacts research quality (per spec Clarification Q2)",
        },
        triggered_retrieval: { bsonType: "bool", description: "Whether this gap triggered a supplementary retrieval" },
        retrieval_status: {
          enum: ["pending", "in_progress", "completed", "failed"],
          description: "Status of the supplementary retrieval for this gap",
        },
        created_at: { bsonType: "date", description: "When the gap was identified" },
      },
    },
  },
  validationLevel: "moderate",
  validationAction: "warn",
});

db.knowledge_gaps.createIndex({ task_id: 1 }, { name: "idx_gap_task_id" });
db.knowledge_gaps.createIndex({ severity: 1 }, { name: "idx_gap_severity" });

// ── Done ────────────────────────────────────────────────────────────────

print(`[init.js] MongoDB ${DB_NAME} initialized: 4 collections created.`);
