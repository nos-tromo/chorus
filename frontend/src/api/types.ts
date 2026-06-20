export interface AppConfig {
  language: 'en' | 'de'
  ingestion_enabled: boolean
  version: string
}

export interface ToolMeta {
  name: string
  description: string
  input_schema: unknown
  output_schema: unknown
}

export type ToolsList = ToolMeta[]

export interface AgentMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AgentTraceEntry {
  tool: string
  arguments: Record<string, unknown>
  error: string | null
  result_count: number | null
}

export interface AgentResponse {
  answer: string
  trace: AgentTraceEntry[]
  truncated: boolean
}

export type JobKind = 'ingest' | 'resolve'

export type JobStatus = 'queued' | 'running' | 'done' | 'error'

export interface JobSnapshot {
  id: string
  kind: JobKind
  status: JobStatus
  result: Record<string, unknown> | null
  error: string | null
}

export interface MigrationsStatus {
  applied: string[]
  pending: string[]
}
