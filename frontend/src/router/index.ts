/**
 * Vue Router configuration.
 *
 * Chat-first routing with ConversationSidebar layout.
 * Auth guards redirect unauthenticated users to login.
 * Auth pages (login/register) render standalone (no sidebar).
 * Authenticated pages render inside AppLayout (with sidebar).
 *
 * Note: authStore.init() is awaited in main.ts before mount,
 * so the router guard runs after session restoration completes.
 */

import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // Authenticated section — wrapped in AppLayout (sidebar + router-view)
    {
      path: '/',
      component: () => import('@/components/layout/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          redirect: '/chat',
        },
        {
          path: 'chat',
          name: 'Chat',
          component: () => import('@/pages/ChatPage.vue'),
        },
        {
          path: 'chat/:conversationId',
          name: 'ChatConversation',
          component: () => import('@/pages/ChatPage.vue'),
        },
        {
          path: 'profile',
          name: 'Profile',
          component: () => import('@/pages/ProfilePage.vue'),
        },
      ],
    },

    // Public pages — standalone, no sidebar
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/pages/LoginPage.vue'),
      meta: { guest: true },
    },
    {
      path: '/register',
      name: 'Register',
      component: () => import('@/pages/RegisterPage.vue'),
      meta: { guest: true },
    },

    // 404 fallback
    {
      path: '/:pathMatch(.*)*',
      redirect: '/chat',
    },
  ],
})

// ── Navigation Guards ─────────────────────────────────────────────────

router.beforeEach((to, _from, next) => {
  // Use Pinia auth store — more reliable than raw localStorage.
  // authStore.init() has already completed in main.ts bootstrap before mount,
  // so isAuthenticated reflects the true login state.
  const authStore = useAuthStore()

  // Authenticated routes — redirect to login if not authenticated
  if (to.matched.some(r => r.meta.requiresAuth) && !authStore.isAuthenticated) {
    next({ name: 'Login', query: { redirect: to.fullPath } })
    return
  }

  // Guest-only pages (login, register) — redirect to /chat if already authed
  if (to.meta.guest && authStore.isAuthenticated) {
    next({ name: 'Chat' })
    return
  }

  next()
})

export default router
