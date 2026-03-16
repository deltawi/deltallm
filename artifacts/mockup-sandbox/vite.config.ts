import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';
import { mockupPreviewPlugin } from './mockupPreviewPlugin';

export default defineConfig({
  base: '/__mockup/',
  plugins: [
    react(),
    tailwindcss(),
    mockupPreviewPlugin(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 23636,
    host: '0.0.0.0',
    allowedHosts: 'all',
    strictPort: true,
  },
});
