import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'

import { GeneratedImage } from './generated-image-result'

vi.mock('./image-generation-placeholder', () => ({ DiffusionCanvas: () => null }))

afterEach(() => {
  cleanup()
  $connection.set(null)
  window.document.documentElement.removeAttribute('data-hermes-desktop-host')
  vi.unstubAllGlobals()
})

// #93508 item 5: a generated image that fails to load offers its file instead. In the browser-hosted
// Desktop that file saves through the ticketed bridge, never a URL carrying the session token.
it('saves a failed browser-hosted image through the ticketed bridge, relative paths included', async () => {
  const saveGatewayFile = vi.fn(async () => ({ saved: true }))
  const openExternal = vi.fn()
  vi.stubGlobal('hermesDesktop', { openExternal, saveGatewayFile })
  window.document.documentElement.dataset.hermesDesktopHost = 'browser'
  $connection.set({ baseUrl: 'http://127.0.0.1:9119', mode: 'remote', token: 'SECRET' } as never)

  const { container, findByText } = render(<GeneratedImage result={{ image: 'renders/out.png', success: true }} />)
  await vi.waitFor(() => expect(container.querySelector('img')).not.toBeNull())
  fireEvent.error(container.querySelector('img')!)
  fireEvent.click(await findByText(/out\.png/))

  expect(saveGatewayFile).toHaveBeenCalledWith(expect.objectContaining({ path: 'renders/out.png' }))
  expect(openExternal).not.toHaveBeenCalled()
})
