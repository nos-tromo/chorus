import { apiGet, apiPost } from './client'
import type { ToolsList } from './types'

export const listTools = () => apiGet<ToolsList>('/tools')

export const callTool = <TOut>(name: string, payload: unknown) =>
  apiPost<TOut>('/tools/' + name, payload)
