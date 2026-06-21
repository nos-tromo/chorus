import { en } from './en'
import { de } from './de'

export type Lang = 'en' | 'de'
// Strings enforces key parity: de must have every key from en, but values are
// plain strings (not the same English literals — that would be absurd).
export type Strings = Record<keyof typeof en, string>
export const catalogs: Record<Lang, Strings> = { en, de }

export function format(
  template: string,
  vars: Record<string, string | number> = {},
): string {
  return template.replace(/\{(\w+)\}/g, (_, k) =>
    k in vars ? String(vars[k]) : `{${k}}`,
  )
}
