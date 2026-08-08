/**
 * Deployment-time configuration for the console (2026-08-08).
 *
 * The OIDC issuer and client id used to be compiled into the bundle, which meant one build per
 * environment — and, in practice, one *published* build pointing at whichever Keycloak the person
 * who ran `ng build` had in mind. A misdirected console does not fail: it sends users to a real
 * login page at the wrong realm, and the error they eventually get names neither.
 *
 * This file ships with the bundle and is loaded before the app. A deployment replaces it — a
 * volume mount, a `ConfigMap`, or a `sed` in the container entrypoint — without rebuilding. The
 * values below are the local Compose stack, so a laptop needs no configuration at all.
 *
 * Nothing secret belongs here: it is served to every browser. An OIDC public client's id is not a
 * secret; a client *secret* would be, which is why this flow does not use one.
 */
window.__AIRA_CONFIG__ = {
  issuer: 'http://localhost:8080/realms/aira',
  clientId: 'aira-gateway',
};
