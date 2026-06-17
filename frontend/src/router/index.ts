/**
 * Vue Router configuration.
 *
 * Phase 3b: Chat-first routing with ConversationSidebar layout.
 * Auth guards redirect unauthenticated users to login.
 */

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // Phase 3b: Chat (primary interaction surface)
    {
      path: '/',
      redirect: '/chat',
    },
    {
      path: '/chat',
      name: 'Chat',
      component: () => import('@/pages/ChatPage.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/chat/:conversationId',
      name: 'ChatConversation',
      component: () => import('@/pages/ChatPage.vue'),
      meta: { requiresAuth: true },
    },
    // Phase 5 (US3): Auth pages
    // { path: '/login', name: 'Login', component: () => import('@/pages/LoginPage.vue') },
    // { path: '/register', name: 'Register', component: () => import('@/pages/RegisterPage.vue') },
    // Phase 7 (US5): Knowledge Base
    // { path: '/knowledge', name: 'KnowledgeBase', component: () => import('@/pages/KnowledgeBasePage.vue') },
    // Phase 5 (US3): Profile
    // { path: '/profile', name: 'Profile', component: () => import('@/pages/ProfilePage.vue') },
  ],
})

// Auth navigation guard (Phase 5 will enhance)
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
  } else {
    next()
  }
})

export default router
