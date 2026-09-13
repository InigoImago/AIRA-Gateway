import { inject } from '@angular/core';
import { CanActivateFn } from '@angular/router';
import { AuthService } from './auth.service';

/** Allow the route when authenticated; otherwise start the OIDC login. */
export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  if (auth.isAuthenticated()) {
    return true;
  }
  // With the issuer unreachable, a login would navigate the page to a host that does not answer
  // and take the shell's explanation with it. Refuse the route and let the explanation stand.
  if (auth.startupError()) {
    return false;
  }
  auth.login();
  return false;
};
