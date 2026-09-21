import { installDemo } from '../interface/demo-api'

installDemo()
// Reuse the existing fail-closed, in-browser transport. This receipt is a
// preview label, never a claim about the installed product version.
const fixtureFetch = window.fetch
window.fetch = async (input, init) => {
  const response = await fixtureFetch(input, init)
  const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
  if (url.pathname.endsWith('/api/status') && response.ok) {
    return new Response(JSON.stringify({ ...(await response.json()), version: 'макет' }), {
      headers: { 'Content-Type': 'application/json' }
    })
  }
  return response
}
void import('./render')
