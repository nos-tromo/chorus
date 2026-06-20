/**
 * ToolSocial — `social_network_around` tool screen.
 *
 * Ego network of follows/friends around an author. Renders a Cytoscape graph
 * with concentric layout (seed centred). Form mirrors 08_social_network_around.py.
 */

import { useState, type FormEvent } from 'react'
import { Banner, Spinner } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useToolCall } from '../hooks/useToolCall'
import { EntityInput } from '../components/form/EntityInput'
import { LimitField } from '../components/form/LimitField'
import { SubmitButton } from '../components/form/SubmitButton'
import { GraphCanvas } from '../components/GraphCanvas'
import { toSocialElements } from '../lib/socialElements'
import { socialStylesheet } from '../lib/graphStyles'
import type { SocialNetworkAroundOut } from '../api/types'
import type cytoscape from 'cytoscape'

// Concentric layout: seed (ring 0) centred. Negate ring so lower ring values
// get higher concentric weight (= placed closer to the centre).
const CONCENTRIC_LAYOUT: cytoscape.ConcentricLayoutOptions = {
  name: 'concentric',
  concentric: (node) => -node.data('ring'),
  levelWidth: () => 1,
}

export function ToolSocial() {
  const t = useT()
  const mutation = useToolCall<SocialNetworkAroundOut>('social_network_around')

  const [author, setAuthor] = useState('')
  const [depth, setDepth] = useState(2)
  const [limit, setLimit] = useState(25)
  const [secondRingLimit, setSecondRingLimit] = useState(50)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    mutation.mutate({ author, depth, limit, second_ring_limit: secondRingLimit })
  }

  const data = mutation.data
  const errorMessage =
    mutation.error instanceof Error
      ? mutation.error.message
      : mutation.error
        ? String(mutation.error)
        : ''

  const followsCount = data ? data.edges.filter((e) => e.kind === 'follows').length : 0
  const friendsCount = data ? data.edges.filter((e) => e.kind === 'friends').length : 0

  return (
    <div className="p-8 space-y-6">
      {/* Title + caption */}
      <div>
        <h1 className="text-2xl font-semibold">{t('social.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t('social.caption')}</p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4 max-w-lg">
        <EntityInput
          label={t('social.author_input')}
          value={author}
          onChange={setAuthor}
          required
        />
        <LimitField
          label={t('social.depth')}
          min={1}
          max={2}
          value={depth}
          onChange={setDepth}
        />
        <div className="grid grid-cols-2 gap-4">
          <LimitField
            label={t('social.limit')}
            min={1}
            max={200}
            value={limit}
            onChange={setLimit}
          />
          <LimitField
            label={t('social.second_ring_limit')}
            min={1}
            max={500}
            value={secondRingLimit}
            onChange={setSecondRingLimit}
          />
        </div>
        <SubmitButton loading={mutation.isPending} disabled={!author}>
          {t('social.build')}
        </SubmitButton>
      </form>

      {/* Error */}
      {mutation.isError && (
        <Banner variant="danger">
          {t('common.tool_call_failed', { error: errorMessage })}
        </Banner>
      )}

      {/* Loading */}
      {mutation.isPending && <Spinner label="…" />}

      {/* Results */}
      {data && !mutation.isPending && (
        <div className="space-y-4">
          {data.nodes.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('social.empty')}</p>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                {t('social.counts', {
                  n: data.nodes.length,
                  edges: data.edges.length,
                  follows: followsCount,
                  friends: friendsCount,
                })}
              </p>
              {data.truncated && (
                <Banner variant="info">{t('social.capped')}</Banner>
              )}
              <GraphCanvas
                elements={toSocialElements(data)}
                stylesheet={socialStylesheet}
                layout={CONCENTRIC_LAYOUT}
              />
            </>
          )}
        </div>
      )}
    </div>
  )
}
