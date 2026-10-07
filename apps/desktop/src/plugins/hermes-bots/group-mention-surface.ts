import { parse, postprocess, preprocess } from 'micromark'
import { gfm } from 'micromark-extension-gfm'
import { parseFragment, Tokenizer } from 'parse5'
import type { Token } from 'parse5'
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
  continuedHandleAt: Set<number>
  quotedLines: Set<number>
}

/** Generated punctuation can follow a complete literal handle. Generated handle characters cannot
 * complete a different handle or turn its literal prefix into another Bot's address. Inline tokenization
 * also sees character references in raw HTML blocks, which document tokens retain as one HTML span. */
function generatedHandleContinuations(source: string): Set<number> {
  const offsets = new Set<number>()
  const events = postprocess(parse().text().write(preprocess()(source, undefined, true)))

  for (const [event, token] of events) {
    if (event !== 'enter' || !GENERATED.has(token.type)) {continue}
    const raw = source.slice(token.start.offset, token.end.offset)
    const fragment = token.type === 'characterReference' ? parseFragment(raw) : null
    const node = fragment?.childNodes[0]
    const decoded = node && 'value' in node ? node.value : raw.slice(1)

    if (/^[\p{L}\p{N}._-]/u.test(decoded)) {offsets.add(token.start.offset)}
  }

  return offsets
}

function markdownLiteralSource(source: string): GroupMentionSurface {
  const surface: string[] = source.split('').map(character => (character === '\n' ? '\n' : ' '))
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

    if (token.type === 'data' || token.type === 'htmlFlow' || token.type === 'htmlText') {
      for (let offset = token.start.offset; offset < token.end.offset; offset++) {
        surface[offset] = source[offset]
      }
    }
  }

  return { text: surface.join(''), continuedHandleAt: generatedHandleContinuations(source), quotedLines }
}

/** Tree construction can merge prose around ignored end tags into one source range. Mask every
 * tokenizer markup span so discarded attributes cannot re-enter through a merged text node. */
function maskHtmlMetadata(source: string, visible: string[]) {
  const hide = (token: Token.Token) => {
    if (!token.location) {return}

    for (let offset = token.location.startOffset; offset < token.location.endOffset; offset++) {
      visible[offset] = source[offset] === '\n' ? '\n' : ' '
    }
  }

  const ignore = () => undefined

  const tokenizer = new Tokenizer({ sourceCodeLocationInfo: true }, {
    onStartTag: hide, onEndTag: hide, onComment: hide, onDoctype: hide,
    onCharacter: ignore, onNullCharacter: ignore, onWhitespaceCharacter: ignore, onEof: ignore
  })

  tokenizer.write(source, true)
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

  maskHtmlMetadata(source.text, visible)

  return { text: visible.join(''), continuedHandleAt: source.continuedHandleAt, quotedLines: source.quotedLines }
}

/** Preserve the existing quote-only Stop rules without returning metadata to the directive surface. */
export function groupDirectiveSurface(value: unknown): string {
  const surface = groupMentionSurface(value)

  return surface.text
    .split('\n')
    .map((line, index) => (surface.quotedLines.has(index) ? `> ${line}` : line))
    .join('\n')
}
