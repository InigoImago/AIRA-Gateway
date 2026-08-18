/**
 * The same prefixes, for Node — the `ng serve` proxy config, which is CommonJS and cannot import
 * the TypeScript module. Both read `prefixes.json`, so there is still one statement of the fact.
 */
module.exports = { PROXY_PREFIXES: require('./prefixes.json') };
