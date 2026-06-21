import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { queryClient } from './api/queryClient'
import { ConfigProvider } from './config/ConfigContext'
import { Shell } from './layout/Shell'
import { AppRoutes } from './routes/Router'

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider>
        <BrowserRouter>
          <Shell>
            <AppRoutes />
          </Shell>
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  )
}
