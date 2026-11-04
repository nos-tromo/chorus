import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { PURPOSE_HEADER, PurposeBar, clearAllPurposes, setPurpose } from '@infra/ui'
import { ApiError, apiGet, apiPost } from './client'
import { PURPOSE_SCOPE } from './purpose'

afterEach(() => {
  clearAllPurposes()
  vi.unstubAllGlobals()
})

function stubFetch(...statuses: number[]) {
  const fetchMock = vi.fn<typeof fetch>()
  for (const status of statuses) {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(status === 428 ? { detail: 'refused' } : { ok: 1 }), { status }),
    )
  }
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The X-Audit-Purpose header of the n-th fetch call, or null when it was not sent. */
function purposeSent(fetchMock: ReturnType<typeof stubFetch>, call: number): string | null {
  const init = fetchMock.mock.calls[call]?.[1] ?? {}
  return new Headers(init.headers as HeadersInit | undefined).get(PURPOSE_HEADER)
}

async function answerPrompt(caseRef: string) {
  const dialog = await screen.findByRole('dialog')
  // The note only shows while a request waits on requestPurpose.
  expect(within(dialog).getByText('This action needs a stated purpose.')).toBeInTheDocument()
  fireEvent.change(within(dialog).getByLabelText('Case reference'), { target: { value: caseRef } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
}

describe('api client: stated purpose', () => {
  it('sends the stated purpose on GET and POST', async () => {
    setPurpose(PURPOSE_SCOPE, { caseRef: 'case-0001', reason: 'Prüfung' })
    const fetchMock = stubFetch(200, 200)
    await apiGet('/stats')
    await apiPost('/tools/posts_mentioning', { entity: 'Example Town' })
    expect(purposeSent(fetchMock, 0)).toBe('case_ref=case-0001&reason=Pr%C3%BCfung')
    expect(purposeSent(fetchMock, 1)).toBe('case_ref=case-0001&reason=Pr%C3%BCfung')
    // The JSON content type survives the merge.
    const init = fetchMock.mock.calls[1]![1]!
    expect((init.headers as Record<string, string>)['content-type']).toBe('application/json')
  })

  it('sends no purpose header when none is stated', async () => {
    const fetchMock = stubFetch(200)
    await apiGet('/stats')
    expect(purposeSent(fetchMock, 0)).toBeNull()
  })

  it('on a 428 asks through the purpose bar and resends once with the purpose', async () => {
    render(<PurposeBar scope={PURPOSE_SCOPE} />)
    const fetchMock = stubFetch(428, 200)
    const pending = apiPost<{ ok: number }>('/tools/posts_mentioning', { entity: 'Example Town' })
    await answerPrompt('case-0001')
    await expect(pending).resolves.toEqual({ ok: 1 })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(purposeSent(fetchMock, 0)).toBeNull()
    expect(purposeSent(fetchMock, 1)).toBe('case_ref=case-0001')
    expect(fetchMock.mock.calls[1]![1]!.body).toBe(JSON.stringify({ entity: 'Example Town' }))
  })

  it('resends an upload with the same form data', async () => {
    render(<PurposeBar scope={PURPOSE_SCOPE} />)
    const fetchMock = stubFetch(428, 202)
    const form = new FormData()
    form.append('files', new Blob(['UUID\r\n']), 'postings.csv')
    const pending = apiPost('/ingestion/ingest', form)
    await answerPrompt('case-0001')
    await pending
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1]![1]!.body).toBe(form)
    expect(purposeSent(fetchMock, 1)).toBe('case_ref=case-0001')
  })

  it('reports the 428 when the prompt is cancelled, without a resend', async () => {
    render(<PurposeBar scope={PURPOSE_SCOPE} />)
    const fetchMock = stubFetch(428)
    const pending = apiGet('/stats')
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    await expect(pending).rejects.toMatchObject({ status: 428 })
    await expect(pending).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('reports the 428 at once when no purpose bar is mounted', async () => {
    const fetchMock = stubFetch(428)
    await expect(apiGet('/stats')).rejects.toMatchObject({ status: 428 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
