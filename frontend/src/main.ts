/**
 * Vue 3 application entry point.
 *
 * Initializes the app with Pinia (state), Vue Router (routing),
 * and Element Plus (UI components). Restores auth session from
 * persisted tokens before mounting to prevent authenticated-route
 * flash when tokens are invalid.
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './assets/global.css'

import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'

async function bootstrap() {
  const app = createApp(App)

  const pinia = createPinia()
  app.use(pinia)
  app.use(router)
  app.use(ElementPlus, { size: 'default' })

  // Restore auth session BEFORE mounting so the router guard
  // sees the correct auth state for the initial navigation.
  const authStore = useAuthStore()
  await authStore.init()

  app.mount('#app')
}

bootstrap()
