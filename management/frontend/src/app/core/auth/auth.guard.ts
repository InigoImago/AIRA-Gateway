import { inject } from '@angular/core';
import { CanActivateFn } from '@angular/router';
import { AuthService } from './auth.service';

/** Allow the route when authenticated; otherwise start the OIDC login. */
export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  if (auth.isAuthenticated()) {
    return true;
  }
  // A login that cannot work is worse than none: with the issuer unreachable, `initCodeFlow`
  // navigates the whole page to a host that does not answer, and the reader loses even the
  // explanation the shell is showing them. Refuse the route and let that explanation stand.
  if (auth.startupError()) {
    return false;
  }
  auth.login();
  return false;
};
