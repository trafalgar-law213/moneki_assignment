import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发模式把 /api 代理到 FastAPI；生产模式由 FastAPI 直接托管 dist（同源，无需代理）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
})
