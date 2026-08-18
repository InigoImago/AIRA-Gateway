import PREFIXES from './prefixes.json';

/**
 * Which URL prefixes belong to AIRA, and which service each one reaches.
 *
 * **This fact was stated in four places**, none of which knew about the others: every call site in
 * the services, `AIRA_PREFIXES` in the auth interceptor, the `location` blocks in the nginx
 * template that serves the built console, and the `ng serve` proxy config. A fifth prefix added to
 * the services and forgotten in the interceptor sends unauthenticated requests, and the failure is
 * a `401` that reads as "your session expired" — the interceptor's own 401 handling would then log
 * a perfectly valid session out.
 *
 * So there is one statement, in `prefixes.json` because both TypeScript and Node's CommonJS
 * (the dev proxy) can read it, and `test_the_console_addresses_one_set_of_prefixes.py` compares it
 * against the nginx template, which cannot read anything.
 *
 * The values carry no trailing slash so that they compose: `` `${API}/v1/models/` ``.
 */
export const API = PREFIXES.management;
export const GW = PREFIXES.gateway;

/** Both prefixes, for anything that has to decide whether a URL is ours — the interceptor. */
export const AIRA_PREFIXES = [`${API}/`, `${GW}/`];
