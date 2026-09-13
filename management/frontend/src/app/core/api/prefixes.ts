import PREFIXES from './prefixes.json';

/**
 * Which URL prefixes belong to AIRA, and which service each one reaches.
 *
 * One statement, in `prefixes.json` so that both this module and the CommonJS dev proxy read it;
 * `test_the_console_addresses_one_set_of_prefixes.py` compares it against the nginx template. A
 * prefix the services use and the interceptor does not know is sent without a token, and its 401
 * logs a valid session out.
 *
 * The values carry no trailing slash so that they compose: `` `${API}/v1/models/` ``.
 */
export const API = PREFIXES.management;
export const GW = PREFIXES.gateway;

/** Both prefixes, for anything that has to decide whether a URL is ours — the interceptor. */
export const AIRA_PREFIXES = [`${API}/`, `${GW}/`];
