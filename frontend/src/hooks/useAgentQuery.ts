import { useMutation } from '@tanstack/react-query'
import { agentQuery } from '../api/agent'
import type { AgentMessage } from '../api/types'

export function useAgentQuery() {
  return useMutation({
    mutationFn: (messages: AgentMessage[]) => agentQuery(messages),
  })
}
