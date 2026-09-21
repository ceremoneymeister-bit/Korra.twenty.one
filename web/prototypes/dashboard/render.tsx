import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import App from '@/App'
import { I18nProvider } from '@/i18n'
import { ThemeProvider } from '@/themes'
import { SystemActionsProvider } from '@/contexts/SystemActions'
import '@/index.css'
import './dashboard.css'

createRoot(document.getElementById('root')!).render(
  <MemoryRouter initialEntries={['/dashboard']}>
    <I18nProvider>
      <ThemeProvider>
        <SystemActionsProvider>
          <App />
        </SystemActionsProvider>
      </ThemeProvider>
    </I18nProvider>
  </MemoryRouter>
)
