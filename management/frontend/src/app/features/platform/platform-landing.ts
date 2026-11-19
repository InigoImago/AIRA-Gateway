import { inject } from '@angular/core';
import { RedirectFunction } from '@angular/router';
import { catchError, map, of } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { Me } from '../../core/api/models';
import { AuthService } from '../../core/auth/auth.service';
import { can } from '../../core/auth/roles';

/**
 * The first platform page this caller may open, in menu order. A caller who may open neither lands
 * on the first, whose page reports the server's refusal rather than showing an empty area.
 */
export function landingFor(me: Me | null): 'content-reads' | 'roles' {
  return !can(me, 'content_read.read') && can(me, 'role.read') ? 'roles' : 'content-reads';
}

/**
 * Where `/platform` goes. Asked of `/me`, so a caller who may read the roles and not the
 * content-read log does not land on a refusal. When `/me` cannot be read the first page is
 * opened, and the shell's header reports why the account is unknown.
 *
 * **Not without a session.** The router runs a redirect before the route's guard, so this is asked
 * first: `/me` without a token answers 401, and the interceptor counts that as a sign-in started
 * beside the one the guard is about to start — two within the loop window read as a login that
 * keeps failing. Without a session the first page is named and the guard signs the reader in.
 */
export const platformLanding: RedirectFunction = () => {
  if (!inject(AuthService).isAuthenticated()) return landingFor(null);
  return inject(MeService)
    .get()
    .pipe(
      map((me) => landingFor(me)),
      catchError(() => of(landingFor(null))),
    );
};
