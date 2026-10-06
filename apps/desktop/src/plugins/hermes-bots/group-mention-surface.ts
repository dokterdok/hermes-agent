import { parse, postprocess, preprocess } from 'micromark'
import { gfm } from 'micromark-extension-gfm'
import { parseFragment } from 'parse5'
import type { DefaultTreeAdapterMap } from 'parse5'

// These are parser token ancestors, not another Markdown scanner.
const METADATA = new Set([
  'codeText',
  'codeFenced',
  'codeIndented',
  'image',
  'definition',
  'resource',
  'reference',
  'autolink',
  'literalAutolink'
])
const GENERATED = new Set(['characterEscape', 'characterReference'])
const HIDDEN_HTML = new Set(['code', 'pre', 'script', 'style', 'template'])

export interface GroupMentionSurface {
  text: string
  generatedAt: Set<number>
  quotedLines: Set<number>
}

function markdownLiteralSource(source: string): GroupMentionSurface {
  const surface: string[] = source.split('').map(character => (character === '\n' ? '\n' : ' '))
  const generatedAt = new Set<number>()
  const quotedLines = new Set<number>()
  const events = postprocess(
    parse({ extensions: [gfm()] })
      .document()
      .write(preprocess()(source, undefined, true))
  )
  let blocked = 0

  for (const [event, token] of events) {
    if (METADATA.has(token.type)) {
      blocked += event === 'enter' ? 1 : -1
    }

    if (event !== 'enter' || blocked) {
      continue
    }

    if (token.type === 'blockQuote') {
      for (let line = token.start.line - 1; line < token.end.line; line++) {
        quotedLines.add(line)
      }
    }

    if (GENERATED.has(token.type)) {
      generatedAt.add(token.start.offset)
    }

    if (token.type === 'data' || token.type === 'htmlFlow' || token.type === 'htmlText') {
      for (let offset = token.start.offset; offset < token.end.offset; offset++) {
        surface[offset] = source[offset]
      }
    }
  }

  return { text: surface.join(''), generatedAt, quotedLines }
}

/** Same CommonMark/GFM grammar as the message renderer, retaining literal source positions.
 * Escapes/entities do not manufacture an address, code and metadata never become prose. */
export function groupMentionSurface(value: unknown): GroupMentionSurface {
  const source = markdownLiteralSource(String(value || ''))
  const tree = parseFragment(source.text, { sourceCodeLocationInfo: true })
  const visible: string[] = source.text.split('').map(character => (character === '\n' ? '\n' : ' '))
  const pending: DefaultTreeAdapterMap['node'][] = [tree]

  while (pending.length) {
    const node = pending.pop()!

    if ('tagName' in node && HIDDEN_HTML.has(node.tagName.toLowerCase())) {
      continue
    }

    if (node.nodeName === '#text' && node.sourceCodeLocation) {
      for (let offset = node.sourceCodeLocation.startOffset; offset < node.sourceCodeLocation.endOffset; offset++) {
        visible[offset] = source.text[offset]
      }
    }

    if ('childNodes' in node) {
      pending.push(...node.childNodes)
    }
  }

  return { text: visible.join(''), generatedAt: source.generatedAt, quotedLines: source.quotedLines }
}

/** Preserve the existing quote-only Stop rules without returning metadata to the directive surface. */
export function groupDirectiveSurface(value: unknown): string {
  const surface = groupMentionSurface(value)

  return surface.text
    .split('\n')
    .map((line, index) => (surface.quotedLines.has(index) ? `> ${line}` : line))
    .join('\n')
}
