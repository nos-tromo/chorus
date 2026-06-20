import { describe, expect, it } from 'vitest'
import { en } from './en'
import { de } from './de'
import { format } from './index'

describe('i18n', () => {
  it('en and de have identical key sets', () => {
    expect(Object.keys(de).sort()).toEqual(Object.keys(en).sort())
  })
  it('interpolates named placeholders', () => {
    expect(format('{n} hits', { n: 3 })).toBe('3 hits')
  })
})
