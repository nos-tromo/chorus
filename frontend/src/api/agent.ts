import { apiPost } from './client'
import type { AgentMessage, AgentResponse } from './types'

export const agentQuery = (messages: AgentMessage[]): Promise<AgentResponse> =>
  apiPost<AgentResponse>('/agent/query', { messages })
