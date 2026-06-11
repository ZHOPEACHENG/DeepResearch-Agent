/**
 * Vue Router configuration.
 *
 * Route definitions will be added progressively as pages are implemented.
 * Navigation guards for auth will be added in Phase 5 (US3).
 */

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // Routes added per-phase:
    // Phase 3 (US2): TaskListPage, TaskDetailPage
    // Phase 4 (US1): ResearchPage, ReportPage
    // Phase 5 (US3): LoginPage, RegisterPage, ProfilePage
    // Phase 7 (US5): KnowledgeBasePage
    // Phase 9 (Polish): DashboardPage
  ],
})

export default router
