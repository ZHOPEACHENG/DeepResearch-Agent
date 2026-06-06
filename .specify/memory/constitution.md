<!--
  ============================================================================
  Sync Impact Report
  ============================================================================
  Version Change: [UNVERSIONED] → 1.0.0 (initial ratification)
  Rationale: First formal project constitution for Academic Deep Research Platform.
    Establishes 7 core principles governing the entire development lifecycle.

  Modified Principles: N/A (initial version)

  Added Sections:
    - Core Principles (7 principles)
    - Security & Data Governance
    - Development Practices
    - Governance

  Removed Sections: N/A (initial version)

  Templates Status:
    ✅ .specify/templates/constitution-template.md — No update needed (source template)
    ✅ .specify/templates/plan-template.md — Constitution Check section is generic;
       gates will be derived from these 7 principles at plan time. No template change needed.
    ✅ .specify/templates/spec-template.md — Requirements & user stories sections
       align with traceability and data isolation principles. No update needed.
    ✅ .specify/templates/tasks-template.md — Task phases support observability,
       logging, and user-story isolation. No update needed.
    ✅ .specify/templates/checklist-template.md — Generic structure; principle-driven
       checklists will be generated at checklist time. No update needed.

  Deferred TODOs: None
  ============================================================================
-->

# Academic Deep Research Platform Constitution

## Core Principles

### I. Research Credibility First

所有研究结论 MUST 保留完整的引用来源链。系统生成的任何分析、总结或结论 MUST
与原始资料内容做出明确区分。用户 MUST 能够从任意结论追溯到其对应的原始资料来源。

- **Source Anchoring**: 每一个事实性断言 MUST 附带至少一条可验证的引用来源。
- **Traceability**: 研究报告中的结论 MUST 支持反向追溯——从结论到中间分析、再到原始
  文献的完整路径 MUST 可被审查。
- **Content Distinction**: AI 生成的分析内容与原始资料摘录 MUST 在输出中清晰区分，
  不得模糊二者边界。用户有权知道哪些内容是系统推理、哪些是原文引用。

**Rationale**: 学术研究的生命线在于可验证性和可复现性。如果用户无法区分"原文如是说"
与"模型推断说"，整个研究成果将失去学术可信度。

### II. Single Responsibility Agents

每个智能体 MUST 承担单一、明确且独立的职责。禁止单一智能体同时负责研究规划、
资料检索、知识整合和报告生成。

- **Agent Boundaries**: 系统 MUST 将研究流水线拆分为职责清晰的独立智能体
  （如 Planner、Retriever、Analyzer、Synthesizer、ReportWriter），
  每个智能体仅负责其边界内的任务。
- **Replaceability**: 任意智能体 MUST 可被独立替换或升级，而不影响其他智能体的
  正常运行。新智能体的加入 MUST NOT 要求修改已有智能体的内部逻辑。
- **Contract-Driven**: 智能体之间的通信 MUST 通过明确定义的输入/输出契约进行，
  不得依赖对方的内部实现细节。

**Rationale**: 单一职责降低复杂度、便于测试、支持并行开发。在研究流程变化的场景下
（如更换搜索引擎、引入新分析模型），独立智能体架构使变更局部化。

### III. Data Persistence & Recoverability

长时间运行的研究任务 MUST 支持状态跟踪、中间结果保存和中断恢复。

- **Stateful Execution**: 任务执行的每个关键步骤 MUST 持久化其状态，
  包括当前阶段、已完成步骤和待处理步骤。
- **Intermediate Checkpoints**: 研究的中间产出（检索结果、初步分析、知识缺口识别等）
  MUST 被保存，而非仅在任务最终完成时一次性写入。
- **Resumability**: 系统故障、重启或用户主动暂停后，任务 MUST 能够从最近的
  检查点恢复执行，而非从头开始。
- **Graceful Degradation**: 当部分子任务失败时，已完成的部分 MUST 被保留；
  用户应能选择重试失败部分或接受部分结果。

**Rationale**: 学术深度研究可能耗时数小时甚至更久。缺乏持久化和恢复能力意味着
任何中断都将导致已投入的时间和计算资源完全浪费，这在用户体验和系统可靠性上
都是不可接受的。

### IV. User Data Isolation

不同用户的数据 MUST 严格隔离。用户只能访问自己的任务、文档、研究成果和相关资源。

- **Tenant Boundary**: 所有数据访问 MUST 在用户维度进行强制过滤。
  任何跨用户数据泄露（查询结果、研究报告、引用来源库）均视为严重缺陷。
- **Ownership**: 每个研究任务、文档和成果 MUST 拥有明确的所属用户标识，
  且该标识在创建后 MUST NOT 可被修改。
- **Access Control**: API 层和业务逻辑层 MUST 各自独立实施用户隔离校验，
  不得依赖单层防护。

**Rationale**: 多用户场景下，数据隔离是安全底线。学术研究常涉及未发表的思路、
敏感数据和竞争性课题，任何数据泄露都可能造成严重后果。

### V. Extensible Architecture

系统架构 MUST 支持未来新增搜索源、知识库、智能体和数据源，新增能力 MUST NOT
破坏现有系统结构。

- **Plugin-Ready Design**: 搜索源（如学术数据库、通用搜索引擎、专业文献库）和
  知识库 MUST 通过统一抽象接口接入，新增来源只需实现接口契约。
- **Agent Registry**: 智能体的注册、发现和调用 MUST 通过抽象层进行，
  使得新增或替换智能体无需修改编排逻辑。
- **Data Source Abstraction**: 外部数据源（API、数据库、文件系统）的接入 MUST
  遵循适配器模式，核心业务逻辑不得直接依赖特定外部系统的实现细节。
- **Backward Compatibility**: 接口变更 MUST 保持向后兼容，或提供明确的迁移路径
  和弃用周期。

**Rationale**: 学术研究领域的数据源和工具在不断演进。锁定单一搜索引擎或
知识库将严重限制平台的适用范围和生命周期。

### VI. Observability & Auditability

关键研究步骤 MUST 保留可审查的执行记录，研究过程 MUST 支持回溯和审查。

- **Execution Trail**: 每个智能体的输入、输出、执行耗时和状态变化 MUST 被记录，
  形成完整的研究过程审计轨迹。
- **Decision Provenance**: 系统做出的关键决策（如"为何选择此文献而非彼文献"、
  "为何判断此处存在知识缺口"）MUST 附带解释性记录。
- **Structured Logging**: 日志 MUST 采用结构化格式，支持按任务、用户、智能体、
  时间范围等维度进行过滤和检索。
- **Non-Repudiation**: 研究输出 MUST 包含生成时间、参与智能体版本、使用的数据源
  等元数据，确保研究过程和结果的可复现性。

**Rationale**: 学术研究需要透明的方法论。如果系统是一个"黑箱"，用户无法理解
研究结论是如何达成的，那么平台在严肃学术场景中的价值将大打折扣。

### VII. Simplicity First

优先实现核心研究工作流，避免引入超出课程项目所需的不必要复杂架构。
系统 MUST 保持可运行、可演示、可维护。

- **Core Workflow First**: MVP MUST 聚焦于"研究规划 → 资料检索 → 知识整合 →
  缺口分析 → 报告生成"这一核心链路，任何不服务于该链路的功能均属于次要优先级。
- **YAGNI**: 在需求尚未明确之前，MUST NOT 引入微服务架构、分布式队列、
  事件溯源等重型基础设施。从单体部署开始，仅在确有必要时才进行拆分。
- **Runnable at All Times**: 主分支在任意时刻 MUST 处于可运行状态。
  演示能力和迭代速度的重要性高于架构完美度。
- **Course-Project Awareness**: 技术选型 MUST 考虑课程项目的维护窗口——
  优先选择学习曲线平缓、文档丰富、社区活跃的方案。
- **Justified Complexity**: 任何超出基础架构的复杂性 MUST 在实现计划中
  明确记录其必要性和被拒绝的更简单替代方案。

**Rationale**: 过早引入复杂架构是课程项目失败的首要原因。一个能够运行、
能够演示、能够说明设计思路的系统远比一个"架构完美但从未完整运行"的系统有价值。

## Security & Data Governance

- **Input Validation**: 所有用户输入 MUST 在进入业务逻辑前经过校验和清理。
- **Error Transparency**: 错误信息 MUST 对开发者提供充分上下文，但对终端用户
  MUST NOT 暴露内部实现细节（如堆栈追踪、数据库结构）。
- **Data Lifecycle**: 用户数据 MUST 支持完整的生命周期管理（创建、访问、导出、删除）。
  删除操作 MUST 级联清理所有关联数据。
- **Configuration Security**: 敏感配置（密钥、连接字符串）MUST NOT 以明文形式
  提交到版本控制系统。

## Development Practices

- **Code Review**: 所有变更 MUST 经过评审后方可合入主分支。
  评审 MUST 检查是否符合本宪法的各项原则。
- **Testing Discipline**: 每个智能体的输入/输出契约 MUST 有对应的自动化测试验证。
  数据隔离逻辑 MUST 有专项测试覆盖。
- **Documentation**: 每个智能体的职责边界、输入输出契约和配置项 MUST 在代码中
  以文档字符串或等效方式明确记录。
- **Versioning**: 智能体接口契约的变更 MUST 反映在版本号中。
  突破性变更 MUST 标注迁移说明。

## Governance

本宪法是 Academic Deep Research Platform 项目的最高指导文件。
所有设计决策、代码评审和架构演进 MUST 以本宪法原则为依归。

- **Amendment Process**: 宪法修订 MUST 经过以下流程：
  (1) 提出修订提案并说明动机；
  (2) 评估对现有代码和流程的影响；
  (3) 更新宪法文档并记录变更日志；
  (4) 同步更新受影响的模板和指引文件。
- **Versioning Policy**: 宪法版本遵循 Semantic Versioning —
  MAJOR 用于原则删除或重新定义，MINOR 用于新增原则或实质性扩展，
  PATCH 用于措辞澄清和排版修正。
- **Compliance Review**: 每次版本发布前 MUST 进行宪法合规性审查，
  确认未引入违反核心原则的变更。
- **Conflict Resolution**: 当本宪法与其他项目文档或惯例出现冲突时，
  以本宪法为准。如有必要，通过修订流程解决冲突。
- **Runtime Guidance**: 日常开发中的具体技术指引参见 `CLAUDE.md` 和
  对应功能分支的 `plan.md` 文件；本宪法聚焦于不可协商的原则约束。

**Version**: 1.0.0 | **Ratified**: 2026-06-04 | **Last Amended**: 2026-06-04
