import path from 'node:path'
import { defineConfig } from 'vite'
import interfacePreview from './vite.interface.config.ts'

// The real product shell with a preview-only route replacement. Production
// builds never import the fixture, its storage, or its synthetic transport.
export default defineConfig({
  ...interfacePreview,
  root: path.join(import.meta.dirname, 'prototypes/dashboard'),
  resolve: {
    ...interfacePreview.resolve,
    alias: [
      {
        find: '@/pages/DashboardPage',
        replacement: path.join(import.meta.dirname, 'prototypes/dashboard/DashboardVisual.tsx')
      },
      ...(interfacePreview.resolve!.alias as { find: string | RegExp; replacement: string }[])
    ]
  },
  build: {
    outDir: path.resolve(import.meta.dirname, '../../dist'),
    emptyOutDir: true,
    sourcemap: false
  },
  server: { host: '127.0.0.1', port: 5186, strictPort: true }
})
