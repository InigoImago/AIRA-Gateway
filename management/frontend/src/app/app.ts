import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { errorMessage } from './core/api/error-message';
import { MeService } from './core/api/me.service';
import { AuthService } from './core/auth/auth.service';
import { PrivacyGate } from './core/privacy/privacy-gate';
import { InstallationClock } from './core/ui/clock';
import { Permission, can } from './core/auth/roles';

@Component({
  selector: 'app-root',
  // `RouterLinkActive` must be imported: an attribute that matches no directive is silently inert,
  // and the navigation could not say which area you were in.
  imports: [RouterOutlet, RouterLink, RouterLinkActive, PrivacyGate],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private readonly meService = inject(MeService);
  private readonly auth = inject(AuthService);

  /** The installation's clock, from `/v1/me` (`FRD-629`). */
  protected readonly clock = inject(InstallationClock).zone;

  protected readonly title = signal('AIRA Gateway');
  /**
   * The account as the console knows it now — the shared answer, not a copy taken at start-up: a
   * screen that reads `/me` after the shell's own read failed, or a role renamed and read again,
   * changes what the header and the navigation show.
   */
  protected readonly me = this.meService.me;

  /** The issuer the console could not reach at startup, or `null` (`AuthService.startupError`). */
  protected readonly startupError = this.auth.startupError;

  /** Why signing in again stopped being tried, or `null` (`AuthService.reauthenticate`). */
  protected readonly loginLoop = this.auth.loginLoop;

  /** Whether a sign-in is under way, which the shell covers (`AuthService.signingIn`). */
  protected readonly signingIn = this.auth.signingIn;

  /** Why the shell's own read of `/me` failed, or `null`. */
  private readonly accountFailure = signal<string | null>(null);

  /**
   * Why the console could not load your account, or `null` when it could.
   *
   * Everything role-shaped in the shell — the username, the role chips, Logout, the incident and
   * oversight navigation — comes from `/me`, so a failure that removes controls has to say so. This
   * is the shell: there is no page below it to carry the message. Taken down as soon as any screen
   * has read the account.
   */
  protected readonly accountError = computed(() => (this.me() ? null : this.accountFailure()));

  ngOnInit(): void {
    if (this.auth.isAuthenticated()) {
      this.meService.get().subscribe({
        error: (error: unknown) =>
          this.accountFailure.set(errorMessage(error, 'Your account could not be loaded.')),
      });
    }
  }

  /** A full reload rather than a retry: what failed happened before the application existed, and
   *  re-running one step would leave the rest of the startup half-done. */
  protected retryStartup(): void {
    window.location.reload();
  }

  /**
   * Whether the server listed this permission for the caller. The template names the permission
   * each entry needs, so the navigation can be read against the server's catalogue (`FRD-614`).
   */
  protected may(permission: Permission): boolean {
    return can(this.me(), permission);
  }

  /**
   * Whether to offer the pipeline-tests screen (`ADR-0020`): the server's `MayRunTests`, an
   * object-level answer that neither the roles nor `use_cases` can reproduce. `?? false`, so an
   * older backend that does not send it offers nothing rather than an entry that 403s.
   */
  protected mayTest(): boolean {
    return this.me()?.may_test ?? false;
  }

  /**
   * One chip per role the caller holds, an installation's own roles included: the slug for
   * machines, the server's name for people (`FRD-614` FR-10). Which role is asking is the first half
   * of "why can I not do this".
   *
   * A name and nothing more. What a role may do is the server's to say, and `/me` lists the
   * caller's permissions rather than one set per role, so a description written here would be a
   * second definition that goes stale whenever a role is edited.
   */
  protected roleChips(): { slug: string; label: string }[] {
    const me = this.me();
    return (me?.roles ?? []).map((slug) => ({ slug, label: me?.role_labels?.[slug] ?? slug }));
  }

  protected logout(): void {
    this.auth.logout();
  }

  /** Leave properly — end the session at the provider, the only way out of a login loop. */
  protected signOutCompletely(): void {
    this.auth.signOutCompletely();
  }
}
