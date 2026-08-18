/**
 * What `ng serve` proxies, and where to.
 *
 * **Was `proxy.conf.json`, with `http://127.0.0.1:8002` and `:8001` written out.** JSON cannot ask
 * anything, so those two were the fifth and sixth statements of ports that had become configurable
 * in Compose — move `AIRA_PUBLISH_GATEWAY_PORT` to dodge a collision and `ng serve` proxied to a
 * port with nothing behind it, answering `504` with no mention of a port anywhere.
 *
 * The prefixes themselves come from `PROXY_PREFIXES`, which the interceptor and the guard test
 * read too: which paths belong to AIRA is one fact, and it was stated in four places.
 */
const { url } = require('../../tools/stack-addresses.cjs');
const { PROXY_PREFIXES } = require('./src/app/core/api/prefixes.cjs');

module.exports = {
  [PROXY_PREFIXES.management]: {
    target: url('management'),
    secure: false,
    changeOrigin: true,
  },
  [PROXY_PREFIXES.gateway]: {
    target: url('gateway'),
    secure: false,
    changeOrigin: true,
    // The console calls `/gw/v1beta/...`; the gateway serves `/v1beta/...`. The prefix is this
    // deployment's routing, not part of the gateway's contract, so it is stripped here and in the
    // nginx config that replaces this proxy in a built image.
    pathRewrite: { [`^${PROXY_PREFIXES.gateway}`]: '' },
  },
};
