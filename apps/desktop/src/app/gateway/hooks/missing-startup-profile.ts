import { getProfiles } from '@/hermes'
import { translateNow } from '@/i18n'
import { notify } from '@/store/notifications'
import { normalizeProfileKey } from '@/store/profile'
import { windowProfileOverride } from '@/store/windows'

/**
 * A Webapp tab boots the profile its `?profile=` or the origin's saved startup
 * route names, and that profile can be renamed or deleted where no hook in this
 * tab sees it: another tab or browser, or `hermes profile` in a terminal. When
 * boot fails on it and the roster loads without it, point the tab and the saved
 * route at Default, as a delete made here does, so the caller can boot again.
 * Only a roster that loads proves the profile gone; a failed read keeps the
 * boot failure. Hosts without the capability (Electron) are untouched.
 */
export async function forgetMissingStartupProfile(startupProfile: null | string | undefined): Promise<boolean> {
  const bridge = window.hermesDesktop?.profile
  const profile = normalizeProfileKey(startupProfile)

  if (!bridge?.forgetProfile || profile === 'default') {
    return false
  }

  try {
    const { profiles } = await getProfiles()

    if (profiles.some(entry => normalizeProfileKey(entry.name) === profile)) {
      return false
    }

    bridge.forgetProfile(profile)

    // A startup source this cannot move would boot the same profile again.
    if (normalizeProfileKey(windowProfileOverride()) === profile || (await bridge.getDefault())?.profile === profile) {
      return false
    }
  } catch {
    return false
  }

  notify({ kind: 'warning', message: translateNow('boot.errors.startupProfileMissing', profile) })

  return true
}
