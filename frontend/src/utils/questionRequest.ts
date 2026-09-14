/** Create once per user operation. Keep the value when resending that operation. */
export function createRequestId(): string {
  // getRandomValues also works on HTTP intranet deployments where randomUUID is unavailable.
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function ensureRequestId(operation: { request_id?: string }): string {
  operation.request_id ??= createRequestId()
  return operation.request_id
}
