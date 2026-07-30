import { apiGet } from './client'
import type { AppConfig, Whoami } from './types'

export const fetchConfig = (): Promise<AppConfig> => apiGet<AppConfig>('/config')

export const getVersion = (): Promise<{ version: string }> =>
  apiGet<{ version: string }>('/version')

/** Signed-in principal served by the backend `/whoami` route (authenticated). */
export const getWhoami = (): Promise<Whoami> => apiGet<Whoami>('/whoami')
