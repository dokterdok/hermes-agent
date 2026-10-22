import { isBrowserHostedDesktop } from '@/lib/platform'

// Primitives and CopyButton enter one policy, never nested fallback retries.
const nativeWrites = new WeakMap<Clipboard, Clipboard['writeText'] | undefined>()

/** An insecure origin (a Webapp opened over plain HTTP from another machine) has no Clipboard API;
 *  the legacy copy command still works there, inside the click that asked for the copy. The
 *  textarea goes beside the focused control, so a dialog or menu that traps focus keeps it in
 *  scope and lets it take focus. True when the browser reports the copy done. */
export function copyTextThroughSelection(text: string): boolean {
  if (typeof document.execCommand !== 'function') {
    return false
  }

  const active = document.activeElement
  const focused = active instanceof HTMLElement && active !== document.body ? active : null
  const selection = document.getSelection()
  const ranges = Array.from({ length: selection?.rangeCount ?? 0 }, (_, index) => selection!.getRangeAt(index))
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0'
  const scope = focused?.parentElement ?? document.body
  scope.append(area)
  area.focus({ preventScroll: true })
  area.select()
  area.setSelectionRange(0, text.length)

  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    area.remove()
    selection?.removeAllRanges()
    ranges.forEach(range => selection?.addRange(range))
    focused?.focus({ preventScroll: true })
  }
}

export async function writeClipboardText(text: string) {
  if (!text) {
    return
  }

  const clipboard = navigator.clipboard

  const native =
    clipboard && (nativeWrites.has(clipboard) ? nativeWrites.get(clipboard) : clipboard.writeText?.bind(clipboard))

  // The browser bridge calls the same native API, not an independent fallback.
  const ipc = isBrowserHostedDesktop() ? undefined : window.hermesDesktop?.writeClipboard

  if (native) {
    try {
      await native(text)

      return
    } catch (error) {
      if (!ipc) {
        throw error
      }
    }
  }

  if (!ipc) {
    if (copyTextThroughSelection(text)) {
      return
    }

    throw new Error('Clipboard API is unavailable')
  }

  if (!(await ipc(text))) {
    throw new Error('Clipboard write is unavailable')
  }
}

export function installClipboardShim() {
  const clipboard = navigator.clipboard

  if (!window.hermesDesktop?.writeClipboard || !clipboard || nativeWrites.has(clipboard)) {
    return
  }

  nativeWrites.set(clipboard, clipboard.writeText?.bind(clipboard))

  try {
    Object.defineProperty(clipboard, 'writeText', { configurable: true, value: writeClipboardText, writable: true })
  } catch {
    // Browser refused override; primitives keep using the native API.
  }
}
