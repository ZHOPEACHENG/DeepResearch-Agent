import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    target: "es2021",
    cssCodeSplit: true,
    minify: "esbuild",
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          // Stable framework chunk — rarely changes
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          // UI library chunk — changes only on Element Plus upgrades
          'vendor-element': ['element-plus'],
          // HTTP client — rarely changes
          'vendor-axios': ['axios'],
        },
      },
    },
  },
});
