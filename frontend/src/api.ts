let csrf = ''
export function setCsrf(value: string) { csrf = value }
export async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (init.method && !['GET', 'HEAD'].includes(init.method.toUpperCase())) headers.set('X-Radar-CSRF', csrf)
  const response = await fetch('/api' + url, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let detail = `Falha HTTP ${response.status}`
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail) } catch { /* response may not be JSON */ }
    if (response.status === 401 && !url.startsWith('/auth/')) window.dispatchEvent(new Event('radar-unauthorized'))
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}
export const json = (method: string, body?: unknown): RequestInit => ({method, body: body === undefined ? undefined : JSON.stringify(body)})
export async function downloadCsv() {
  const response = await fetch('/api/export.csv', {credentials: 'same-origin'})
  if (!response.ok) throw new Error('Não foi possível exportar os dados')
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = 'radar-grupos.csv'; a.click()
  URL.revokeObjectURL(url)
}
