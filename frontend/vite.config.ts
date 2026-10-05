import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'


function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return path.resolve(__dirname, 'src/assets', filename)
      }
    },
  }
}

export default defineConfig({
  plugins: [
    figmaAssetResolver(),
    // The React and Tailwind plugins are both required for Make, even if
    // Tailwind is not being actively used – do not remove them
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // Alias @ to the src directory
      '@': path.resolve(__dirname, './src'),
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ['**/*.svg', '**/*.csv'],

  server: {
    // Proxy all backend routes to the local API server so the browser avoids CORS issues.
    // Change the target if your backend runs on a different port.
    proxy: {
      '/auth': 'http://localhost:3000',
      '/upload': 'http://localhost:3000',
      '/watermark': 'http://localhost:3000',
      '/images': 'http://localhost:3000',
      '/image': 'http://localhost:3000',
      '/files': 'http://localhost:3000',
      '/verify': 'http://localhost:3000',
      // Regex so it doesn't also catch Vite's own /src/... files.
      '^/s/': 'http://localhost:3000',
    },
  },
})
