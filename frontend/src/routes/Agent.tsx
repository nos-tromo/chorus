import { useState, useRef, type FormEvent, type KeyboardEvent } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Banner, Button, CopyButton, Input, Spinner } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useAgentQuery } from '../hooks/useAgentQuery'
import { ToolTrace } from '../components/ToolTrace'
import type { AgentMessage, AgentTraceEntry } from '../api/types'

// ── Local message type (adds optional trace for assistant turns) ──────────────

interface ConversationTurn {
  role: 'user' | 'assistant'
  content: string
  trace?: AgentTraceEntry[]
  truncated?: boolean
}

// ── Agent ─────────────────────────────────────────────────────────────────────

export function Agent() {
  const t = useT()
  const mutation = useAgentQuery()

  const [turns, setTurns] = useState<ConversationTurn[]>([])
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  async function send() {
    const text = input.trim()
    if (!text || mutation.isPending) return

    // Append the user turn and capture the full history to send.
    const userTurn: ConversationTurn = { role: 'user', content: text }
    const nextTurns = [...turns, userTurn]
    setTurns(nextTurns)
    setInput('')

    // Build the message array for the API (role + content only).
    const apiMessages: AgentMessage[] = nextTurns.map((m) => ({
      role: m.role,
      content: m.content,
    }))

    try {
      const result = await mutation.mutateAsync(apiMessages)
      const assistantTurn: ConversationTurn = {
        role: 'assistant',
        content: result.answer || t('agent.no_answer'),
        trace: result.trace,
        truncated: result.truncated,
      }
      setTurns((prev) => [...prev, assistantTurn])
      // Scroll to bottom after render.
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 0)
    } catch {
      // Error is surfaced via mutation.isError / mutation.error below.
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void send()
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send()
    }
  }

  function clearConversation() {
    setTurns([])
    setInput('')
    mutation.reset()
  }

  const errorMessage =
    mutation.error instanceof Error
      ? mutation.error.message
      : mutation.error
        ? String(mutation.error)
        : ''

  return (
    <div className="p-8 space-y-6 max-w-3xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold">{t('agent.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t('agent.caption')}</p>
      </div>

      {/* Clear button */}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={clearConversation}
        disabled={turns.length === 0 && !mutation.isError}
      >
        {t('agent.clear')}
      </Button>

      {/* Conversation */}
      {turns.length > 0 && (
        <div className="space-y-4">
          {turns.map((turn, idx) => (
            <div
              key={idx}
              className={
                turn.role === 'user'
                  ? 'flex justify-end'
                  : 'flex justify-start'
              }
            >
              <div
                className={
                  turn.role === 'user'
                    ? 'max-w-prose rounded-lg bg-accent text-accent-foreground px-4 py-2 text-sm'
                    : 'relative group max-w-prose rounded-lg border border-border bg-card px-4 py-2 text-sm space-y-2'
                }
                data-testid={turn.role === 'user' ? 'user-bubble' : 'assistant-bubble'}
              >
                {turn.role === 'user' ? (
                  <p className="whitespace-pre-wrap">{turn.content}</p>
                ) : (
                  <>
                    <div className="prose prose-invert prose-sm max-w-none overflow-x-auto prose-pre:bg-muted prose-code:before:content-none prose-code:after:content-none">
                      <Markdown remarkPlugins={[remarkGfm]}>{turn.content}</Markdown>
                    </div>
                    <CopyButton
                      text={turn.content}
                      label={t('common.copy')}
                      copiedLabel={t('common.copied')}
                      className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                    />
                  </>
                )}

                {turn.role === 'assistant' && turn.trace && turn.trace.length > 0 && (
                  <ToolTrace trace={turn.trace} />
                )}

                {turn.truncated && (
                  <Banner variant="info" className="mt-2 text-xs" data-testid="truncation-notice">
                    {t('agent.truncated')}
                  </Banner>
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}

      {/* Thinking spinner */}
      {mutation.isPending && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner label={t('agent.thinking')} />
        </div>
      )}

      {/* Error banner */}
      {mutation.isError && (
        <Banner variant="danger">
          {t('agent.call_failed', { error: errorMessage })}
        </Banner>
      )}

      {/* Input form */}
      <form onSubmit={handleSubmit} className="flex gap-2 items-center">
        <Input
          className="flex-1"
          placeholder={t('agent.chat_input')}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={mutation.isPending}
          data-testid="agent-input"
        />
        <Button
          type="submit"
          variant="primary"
          disabled={!input.trim() || mutation.isPending}
        >
          {t('common.search')}
        </Button>
      </form>
    </div>
  )
}
