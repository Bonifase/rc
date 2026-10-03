/**
 * Base URL for the Flask API.
 *
 * Local dev: leave VITE_API_URL unset. Requests go to a relative "/api/..."
 * path, which Vite's dev server proxies to http://localhost:5000 (see
 * vite.config.js).
 *
 * Production (e.g. Vercel): set VITE_API_URL to your deployed backend's
 * URL (e.g. Render), such as "https://citation-checker-api.onrender.com".
 * No relative-path proxy exists in production, so this must point directly
 * at the live backend.
 */
export const API_BASE_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '')

export function apiUrl(path) {
  return `${API_BASE_URL}${path}`
}

/**
 * Optional shared secret for the backend's API_KEY auth (see
 * webapp/backend/app.py). Leave VITE_API_KEY unset for local dev, or for a
 * deployment where the backend's API_KEY is also unset -- both sides default
 * to "no auth" together. If the backend has API_KEY set, this must match it.
 */
export const API_KEY = (import.meta.env.VITE_API_KEY || '').trim()

/**
 * Merges the X-API-Key header into a fetch() headers object when a key is
 * configured, so we don't sprinkle "if (API_KEY)" checks at every call site.
 * A no-op when API_KEY is unset, so existing unauthenticated deployments are
 * unaffected.
 */
export function apiHeaders(extra = {}) {
  return API_KEY ? { ...extra, 'X-API-Key': API_KEY } : extra
}
