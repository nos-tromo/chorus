import { apiGet } from './client'
import type { AppConfig } from './types'

export const fetchConfig = (): Promise<AppConfig> => apiGet<AppConfig>('/config')
