import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { queryClient } from './api/queryClient'
import { ConfigProvider } from './config/ConfigContext'
import { Shell } from './layout/Shell'
import { ProjectProvider, ProjectScoped } from './project/ProjectContext'
import { AppRoutes } from './routes/Router'

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider>
        <ProjectProvider>
          <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/+$/, '')}>
            {/* Shell sits outside ProjectScoped so the switcher and the
                chrome around it survive the remount they trigger. */}
            <Shell>
              <ProjectScoped>
                <AppRoutes />
              </ProjectScoped>
            </Shell>
          </BrowserRouter>
        </ProjectProvider>
      </ConfigProvider>
    </QueryClientProvider>
  )
}
