/** Private native IPC. Ref values are random handles, never bearer encodings. */
export interface RoomSecretEntry {
  scope: string[]
  value?: string
  ref?: string
}
export interface RoomSecretRequest {
  action: 'seal' | 'open' | 'preflight'
  entries: RoomSecretEntry[]
}
export interface RoomStorageLockRequest {
  action: 'lock' | 'unlock'
  key: string
  token?: string
}
export type RoomSecretReply = { ok: true; values: string[] } | { ok: false; error: string }
export interface RoomSecretApi {
  exchange(request: RoomSecretRequest): string[]
  lock(key: string): string
  unlock(key: string, token: string): void
}
export const ROOM_SECRET_CHANNEL = 'hermes:room-secrets:exchange'
