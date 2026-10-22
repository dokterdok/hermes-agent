import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'
import { clearWebappSession, seedWebappSession } from '@/test/webapp-session'

import { installBrowserDesktopBridge } from './browser-desktop-bridge'

type BootstrapWindow = Window & { __HERMES_BASE_PATH__?: string; }
const win = window as unknown as BootstrapWindow

/** A fresh page load: the bridge is rebuilt from scratch, only storage survives. */
function reload(basePath = '') {
  Reflect.deleteProperty(win, 'hermesDesktop')
  win.__HERMES_BASE_PATH__ = basePath
  expect(installBrowserDesktopBridge()).toBe(true)

  return win.hermesDesktop!.settings
}

function onProfile(profile: string) {
  $connection.set({ ...($connection.get() ?? {}), profile } as NonNullable<ReturnType<typeof $connection.get>>)
}

beforeEach(() => {
  seedWebappSession('served-token')
  localStorage.clear()
})

afterEach(() => {
  Reflect.deleteProperty(win, 'hermesDesktop')
  delete win.__HERMES_BASE_PATH__
  clearWebappSession()
  $connection.set(null)
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('browser default project folder', () => {
  it('keeps a chosen folder across reloads, per hosted gateway and profile, until cleared', async () => {
    onProfile('work')
    await expect(reload('/first').setDefaultProjectDir('/srv/repos/app')).resolves.toEqual({ dir: '/srv/repos/app' })

    expect((await reload('/first').getDefaultProjectDir()).dir).toBe('/srv/repos/app')
    // Another gateway on this origin, or another profile, browses a different machine's folders.
    expect((await reload('/second').getDefaultProjectDir()).dir).toBeNull()
    onProfile('ssh-box')
    expect((await reload('/first').getDefaultProjectDir()).dir).toBeNull()

    onProfile('work')
    await reload('/first').setDefaultProjectDir(null)
    expect((await reload('/first').getDefaultProjectDir()).dir).toBeNull()
  })

  it('moves a folder with a rename the server accepted, and forgets it on delete', async () => {
    const replies: unknown[] = [409, { ok: true }, { ok: true }]

    const reply = vi.fn(async () => {
      const next = replies.shift()

      return typeof next === 'number' ? new Response('{}', { status: next }) : new Response(JSON.stringify(next))
    })

    vi.stubGlobal('fetch', reply)
    const settings = reload()

    const dirs = async () => {
      onProfile('work')
      const work = (await settings.getDefaultProjectDir()).dir
      onProfile('renamed')

      return { renamed: (await settings.getDefaultProjectDir()).dir, work }
    }

    onProfile('work')
    await settings.setDefaultProjectDir('/srv/work')
    onProfile('renamed') // left behind by an earlier profile of that name
    await settings.setDefaultProjectDir('/srv/stale')
    const rename = { body: { new_name: 'renamed' }, method: 'PATCH', path: '/api/profiles/work' }

    await expect(win.hermesDesktop!.api(rename)).rejects.toThrow('409')
    expect(await dirs()).toEqual({ renamed: '/srv/stale', work: '/srv/work' })

    await win.hermesDesktop!.api(rename)
    expect(await dirs()).toEqual({ renamed: '/srv/work', work: null })

    await win.hermesDesktop!.api({ method: 'DELETE', path: '/api/profiles/renamed' })
    expect(await dirs()).toEqual({ renamed: null, work: null })
    vi.unstubAllGlobals()
  })

  it('rejects a choice this browser cannot keep instead of reporting it saved', async () => {
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('storage denied')
    })

    await expect(reload().setDefaultProjectDir('/srv/repos/app')).rejects.toThrow()
    expect((await reload().getDefaultProjectDir()).dir).toBeNull()
  })
})
