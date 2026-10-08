import { renderHook } from '@testing-library/react'
import { expect, it, onTestFinished, vi } from 'vitest'

const active = vi.hoisted(() => ({ locale: 'en' }))

vi.mock('@hermes/plugin-sdk', () => ({
  useI18n: () => ({ t: TRANSLATIONS[active.locale as keyof typeof TRANSLATIONS] }),
  usePluginI18n: (pluginId: string) => (key: string, ...args: unknown[]) =>
    translatePlugin(pluginId, active.locale, key, args)
}))
vi.mock('../plugins/hermes-bots/shared', () => ({ getPluginCtx: () => null }))

import { useCanonicalGroupLabels } from '../plugins/hermes-bots/canonical-group-labels'
import { BOTS_LOCALES } from '../plugins/hermes-bots/i18n'

import { TRANSLATIONS } from './catalog'
import { registerPluginLocales, translatePlugin } from './plugin-i18n'

it('provides translated canonical group controls and recovery copy in every supported locale', () => {
  onTestFinished(registerPluginLocales('hermes-bots', BOTS_LOCALES))
  const english = BOTS_LOCALES.en?.canonical as Record<string, string> | undefined
  expect(english).toBeDefined()

  for (const locale of Object.keys(TRANSLATIONS) as Array<keyof typeof TRANSLATIONS>) {
    active.locale = locale
    const { result, unmount } = renderHook(useCanonicalGroupLabels)
    expect(result.current.back).toBe(TRANSLATIONS[locale].common.back)
    expect(result.current.download).toBe(TRANSLATIONS[locale].fileMenu.download)
    const messages = BOTS_LOCALES[locale]?.canonical as Record<string, string> | undefined
    expect(messages, locale).toBeDefined()
    expect(Object.keys(messages!).sort(), locale).toEqual(Object.keys(english!).sort())

    const sharedGroup = BOTS_LOCALES[locale]?.group as Record<string, unknown> | undefined
    const englishGroup = BOTS_LOCALES.en?.group as Record<string, unknown>

    for (const [key, value] of Object.entries(messages!)) {
      const expected = key === 'you' ? sharedGroup?.you ?? englishGroup.you : value
      expect(result.current[key as keyof typeof result.current], `${locale}.${key}`).toBe(expected)
      expect(value.trim(), `${locale}.${key}`).not.toBe('')
    }

    // Shared vocabulary may match English; recovery copy must still use this locale.
    if (locale !== 'en') {
      for (const key of ['unconfirmedSend', 'restorePendingSend', 'pendingActionUnconfirmed'] as const) {
        expect(result.current[key], `${locale}.${key}`).not.toBe(english![key])
      }
    }

    unmount()
  }
})
