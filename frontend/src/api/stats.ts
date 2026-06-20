import { apiGet } from './client'
import type { GraphStats } from './types'

export const fetchStats = () => apiGet<GraphStats>('/stats')
