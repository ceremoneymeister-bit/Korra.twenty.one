declare module 'sanitize-html' {
  interface Sanitize {
    (html: string, options?: unknown): string
    defaults: { allowedTags: string[] }
  }

  const sanitize: Sanitize
  export default sanitize
}

declare module 'three'
