/**
 * What a deployment may set without rebuilding the bundle (`public/runtime-config.js`), so one
 * build serves every environment. Written at container start (`deploy/10-runtime-config.envsh`).
 */
export interface RuntimeConfig {
  issuer?: string;
  clientId?: string;
  /**
   * Where a trace is opened, with `{trace_id}` in place of the id — a Grafana or Tempo address.
   * Empty when the installation names none: the id is then shown and copied, not linked.
   */
  traceUrl?: string;
}

/**
 * Read at the point of use, not at import: the bundle loads before anything can arrange for
 * `runtime-config.js` to be there, and a module-level read would fix the console's identity then.
 */
export function runtimeConfig(): RuntimeConfig {
  return (window as unknown as { __AIRA_CONFIG__?: RuntimeConfig }).__AIRA_CONFIG__ ?? {};
}

/**
 * The address that opens ``traceId`` in the installation's trace viewer, or `null` where none is
 * configured, the template does not name the id, or it is not a web address — a link that goes
 * nowhere, or to a `javascript:` URL, is worse than none.
 */
export function traceViewerUrl(traceId: string | null | undefined): string | null {
  const template = runtimeConfig().traceUrl?.trim();
  if (!traceId || !template || !template.includes('{trace_id}')) return null;
  if (!/^https?:\/\//i.test(template)) return null;
  return template.split('{trace_id}').join(encodeURIComponent(traceId));
}
