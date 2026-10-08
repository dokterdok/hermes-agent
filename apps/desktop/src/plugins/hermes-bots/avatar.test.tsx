/**
 * The avatar face: blobatar shape strings, the markup they render to, and the
 * catchlight polarity fix.
 *
 * Shape strings are stored per bot and must round-trip forever —
 * `blobatar[:seed[:kind]]`, where an unlocked seed follows the bot's name and
 * an unknown silhouette is ignored rather than trusted. The silhouette is
 * pinned by handing the library a TRAIT position inside its frozen band, so
 * the band a kind lands in is a stored-appearance contract, not an
 * implementation detail.
 *
 * Catchlight (image14 report, Aug 2026): the sparkle's contrast follows the
 * PUPIL, not the body. Dark bodies flip the pupils to light cream, and a white
 * catchlight on a cream pupil is invisible — maroon/ink/oxblood avatars looked
 * like they had "no dots in their eyes".
 */

import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { blobatarSvgMock } = vi.hoisted(() => ({ blobatarSvgMock: vi.fn() }))

vi.mock('@hermes/plugin-sdk', async () => {
  const { atom } = await import('nanostores')

  return {
    atom,
    blobatarSvg: (seed: string, opts: unknown) => blobatarSvgMock(seed, opts) as string,
    createBudgetedLoop: undefined,
    host: { state: { connectionId: { get: () => 'local' } } },
    profileColor: (name: string) => (name === 'inbox-triage' ? '#38bdf8' : '#8b5cf6'),
    PROFILE_SWATCHES: ['#38bdf8', '#8b5cf6'],
    queryClient: undefined,
    useQuery: vi.fn(),
    useValue: vi.fn()
  }
})

vi.mock('./shared', () => ({ getPluginCtx: () => null, ID: 'hermes-bots' }))

/** The library's frozen band per silhouette (gen2 thresholds). */
const BANDS: Record<string, [number, number]> = {
  boxy: [0.48, 0.6],
  capsule: [0.6, 0.7],
  cloud: [0.79, 0.86],
  droplet: [0.86, 0.915],
  hexagon: [0.915, 0.95],
  nub: [0.7, 0.79],
  organic: [0.22, 0.48],
  round: [0, 0.22],
  sun: [0.95, 0.98],
  triangle: [0.98, 1]
}

interface BlobOptions {
  size: number
  traits?: { shape: number }
}

const lastBlobCall = () => blobatarSvgMock.mock.calls.at(-1) as [string, BlobOptions]

beforeEach(() => {
  vi.clearAllMocks()
  blobatarSvgMock.mockReturnValue('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>')
})

describe('blob shape strings round-trip through parse/build', () => {
  it('recognizes only the blobatar family', async () => {
    const { isBlobShape } = await import('./avatar')

    expect(isBlobShape('blobatar')).toBe(true)
    expect(isBlobShape('blobatar:seed123')).toBe(true)
    expect(isBlobShape('blobatar::sun')).toBe(true)
    expect(isBlobShape('circle')).toBe(false)
    expect(isBlobShape(undefined)).toBe(false)
  })

  it('parses the seed/silhouette segments, ignoring an unknown silhouette', async () => {
    const { parseBlobShape } = await import('./avatar')

    // Unlocked: the seed follows the name.
    expect(parseBlobShape('blobatar', 'inbox-triage')).toEqual({ kind: '', seed: 'inbox-triage', seedPart: '' })
    expect(parseBlobShape('blobatar:abc123', 'inbox-triage')).toEqual({
      kind: '',
      seed: 'abc123',
      seedPart: 'abc123'
    })
    expect(parseBlobShape('blobatar::cloud', 'inbox-triage')).toEqual({
      kind: 'cloud',
      seed: 'inbox-triage',
      seedPart: ''
    })
    expect(parseBlobShape('blobatar:abc:mystery', 'x').kind).toBe('')
  })

  it('rebuilds every segment combination', async () => {
    const { blobShapeString } = await import('./avatar')

    expect(blobShapeString('', '')).toBe('blobatar')
    expect(blobShapeString('abc', '')).toBe('blobatar:abc')
    expect(blobShapeString('abc', 'sun')).toBe('blobatar:abc:sun')
    expect(blobShapeString('', 'sun')).toBe('blobatar::sun')
  })
})

describe('rendering a blob face', () => {
  it('tags the markup data-bot-face so the roster PNG backfill can find it', async () => {
    // pushLocalAvatars → rasterizeSvgToPng queries `svg[data-bot-face=…]`;
    // without the tag a vector face never reaches the inter-agent notices.
    const { BotFace } = await import('./avatar')
    const { container } = render(<BotFace color="#38bdf8" name="inbox-triage" shape="blobatar" size={56} />)

    expect(container.querySelector('svg[data-bot-face="inbox-triage"]')).toBeTruthy()

    const [seed, opts] = lastBlobCall()

    expect(seed).toBe('inbox-triage')
    expect(opts.size).toBe(56)
    // No pinned silhouette means no traits at all — the library picks.
    expect('traits' in opts).toBe(false)
  })

  it('puts every silhouette inside its own frozen band', async () => {
    const { BLOB_KINDS, BotFace } = await import('./avatar')

    for (const kind of BLOB_KINDS) {
      render(<BotFace color="#38bdf8" name="agent" shape={`blobatar::${kind}`} size={32} />)

      const trait = lastBlobCall()[1].traits?.shape

      expect(typeof trait, kind).toBe('number')
      expect(trait, kind).toBeGreaterThanOrEqual(BANDS[kind][0])
      expect(trait, kind).toBeLessThan(BANDS[kind][1])
    }
  })

  it('falls back to the legacy math face when the renderer throws', async () => {
    blobatarSvgMock.mockImplementation(() => {
      throw new Error('boom')
    })

    const { BotFace } = await import('./avatar')
    const { container } = render(<BotFace color="#38bdf8" name="agent" shape="blobatar" size={32} />)

    expect(container.querySelector('svg[data-hb-math]')).toBeTruthy()
  })
})

it.each(['default', 'inbox-triage'])('group faces use the owner profile %s consistently across seats and match the roster primitive without asset discovery', async profile => {
  const { avatarColor, botAppearance, BotFace } = await import('./avatar')
  const { CanonicalMemberFace, canonicalMemberName } = await import('./canonical-group-identity')
  const appearance = botAppearance(profile, undefined)
  const first = {member_id: 'member-1', profile, handle: 'owner-handle', display_name: 'Owner label'}
  const second = {...first, member_id: 'member-5', display_name: 'Another room label'}

  const view = render(<>
    <section data-testid="group-a"><CanonicalMemberFace member={first} name={first.display_name} /></section>
    <section data-testid="group-b"><CanonicalMemberFace member={second} name={second.display_name} /></section>
    <section data-testid="roster"><BotFace color={avatarColor(appearance.color, profile)} name={profile} shape={appearance.shape} size={24} /></section>
  </>)

  const traits = (scope: string) => {
    const svg = view.getByTestId(scope).querySelector('svg')!

    return [svg.getAttribute('data-hb-shape'), svg.querySelector('[data-hb-body]')?.getAttribute('fill')]
  }

  expect(traits('group-a')).toEqual(traits('roster'))
  expect(traits('group-b')).toEqual(traits('roster'))
  expect(view.getByTestId('group-a').querySelector('svg[data-bot-face]')).toBeNull()
  expect(view.getByTestId('group-b').querySelector('svg[data-bot-face]')).toBeNull()
  expect(view.getByTestId('roster').querySelector('svg[data-bot-face]')).not.toBeNull()
  expect(view.container.querySelector('img')).toBeNull()
  expect(canonicalMemberName(first, 'Unknown')).toBe(first.display_name)
  expect(canonicalMemberName(second, 'Unknown')).toBe(second.display_name)
})

it('keeps the existing member or event fallback when the group owner supplied no profile', async () => {
  const { avatarColor, botAppearance, BotFace } = await import('./avatar')
  const { CanonicalMemberFace } = await import('./canonical-group-identity')
  const legacy = {member_id: 'legacy-seat', profile: '', handle: 'legacy', display_name: 'Owner label'}

  const view = render(<>
    <section data-testid="legacy"><CanonicalMemberFace member={legacy} name="Owner label" seed="ignored-event" /></section>
    <section data-testid="event"><CanonicalMemberFace name="Unknown Bot" seed="event-author" /></section>
    {['legacy-seat', 'event-author'].map(seed => {
      const appearance = botAppearance(seed, undefined)

      return <section data-testid={seed} key={seed}><BotFace color={avatarColor(appearance.color, seed)} name={seed} shape={appearance.shape} size={24} /></section>
    })}
  </>)

  const traits = (scope: string) => {
    const svg = view.getByTestId(scope).querySelector('svg')!

    return [svg.getAttribute('data-hb-shape'), svg.querySelector('[data-hb-body]')?.getAttribute('fill')]
  }

  expect(traits('legacy')).toEqual(traits('legacy-seat'))
  expect(traits('event')).toEqual(traits('event-author'))
})

it('keeps canonical blob SVGs out of avatar backfill while preserving the roster primitive geometry and profile seed', async () => {
  const avatar = await import('./avatar')
  const { CanonicalMemberFace } = await import('./canonical-group-identity')
  const profile = 'blob-owner'
  const appearance = {...avatar.botAppearance(profile, undefined), shape: 'blobatar'}
  const policy = vi.spyOn(avatar, 'botAppearance').mockReturnValue(appearance)

  try {
    const view = render(<>
      <section data-testid="canonical-blob"><CanonicalMemberFace member={{member_id: 'generated-seat', profile, handle: 'owner'}} name="Owner label" /></section>
      <section data-testid="roster-blob"><avatar.BotFace color={avatar.avatarColor(appearance.color, profile)} name={profile} shape={appearance.shape} size={24} /></section>
    </>)

    const canonical = view.getByTestId('canonical-blob').querySelector('svg')!
    const roster = view.getByTestId('roster-blob').querySelector('svg')!
    expect(canonical.hasAttribute('data-bot-face')).toBe(false)
    expect(roster.getAttribute('data-bot-face')).toBe(profile)
    const geometry = roster.cloneNode(true) as SVGElement
    geometry.removeAttribute('data-bot-face')
    expect(canonical.outerHTML).toBe(geometry.outerHTML)
    expect(blobatarSvgMock.mock.calls.map(call => call[0])).toEqual([profile, profile])
  } finally {policy.mockRestore()}
})

// Last: re-mocking the SDK re-links the whole avatar graph, so anything after
// this would be running against the swapped module.
describe('an SDK that predates blobatarSvg', () => {
  it('renders the legacy deterministic shape instead of nothing', async () => {
    vi.resetModules()
    vi.doMock('@hermes/plugin-sdk', async () => {
      const { atom } = await import('nanostores')

      return {
        atom,
        blobatarSvg: undefined,
        createBudgetedLoop: undefined,
        host: { state: { connectionId: { get: () => 'local' } } },
        profileColor: () => '#8b5cf6',
        PROFILE_SWATCHES: [],
        queryClient: undefined,
        useQuery: vi.fn(),
        useValue: vi.fn()
      }
    })

    const { BotFace, defaultShapeFor } = await import('./avatar')
    const { container } = render(<BotFace color="#38bdf8" name="agent" shape="blobatar" size={32} />)

    expect(container.querySelector('svg[data-hb-math]')?.getAttribute('data-hb-shape')).toBe(defaultShapeFor('agent'))
  })
})
