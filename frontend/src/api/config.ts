import { apiGet } from './client'
import type { AppConfig } from './types'

export const fetchConfig = (): Promise<AppConfig> => apiGet<AppConfig>('/config')

export const getVersion = (): Promise<{ version: string }> =>
  apiGet<{ version: string }>('/version')
