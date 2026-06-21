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

/** Response from job-start endpoints (POST /ingestion/ingest, POST /ingestion/resolve). */
export interface JobAccepted {
  job_id: string
  status: JobStatus
  kind: JobKind
}

/** Response from the job-poll endpoint (GET /ingestion/jobs/{id}). */
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

// --- stats ---

export interface GraphStats {
  counts: {
    posts: number
    authors: number
    entities: number
    hashtags: number
    groups: number
    platforms: number
    aliases: number
  }
  edges: {
    mentions: number
    authored: number
    follows: number
    friends: number
    resolved: number
  }
  top_entities: { name: string; count: number }[]
  top_authors: { author_id: string; label: string; count: number }[]
  posts_by_platform: { platform: string; count: number }[]
  latest_ingested_at: string | null
  resolution: {
    resolved_aliases: number
    total_aliases: number
  }
}

// --- network_around ---

export interface NetworkNode {
  id: string
  kind: 'author' | 'topic'
  label: string
  entity_id: string | null
  is_seed: boolean
}

export interface NetworkEdge {
  source: string
  target: string
  weight: number
}

export interface NetworkAroundOut {
  seed: string
  seed_node_id: string | null
  nodes: NetworkNode[]
  edges: NetworkEdge[]
  truncated: boolean
}

// --- social_network_around ---

export interface SocialNode {
  id: string
  label: string
  ring: number
  is_seed: boolean
}

export interface SocialEdge {
  source: string
  target: string
  kind: 'follows' | 'friends'
  directed: boolean
}

export interface SocialNetworkAroundOut {
  seed: string
  seed_node_id: string | null
  nodes: SocialNode[]
  edges: SocialEdge[]
  truncated: boolean
}
