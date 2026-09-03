import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // Keep the copied upstream tree byte-for-byte reviewable instead of forcing
  // our project-specific lint rules across all 148 source files. Components we
  // intentionally customize are linted explicitly in the verification run.
  globalIgnores(['dist', 'src/vendor/nous-ui/**']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Context providers and hook files commonly export both a component
      // (the Provider) and a hook (useContext). Allow constant exports so
      // these don't need to be split into separate files.
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // TODO: upgrade these react-hooks v7 rules from 'warn' to 'error' after
      // refactoring set-state-in-effect, ref-as-instance-var, and manual
      // memoization patterns in the web codebase.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      'react-hooks/static-components': 'warn',
    },
  },
])
