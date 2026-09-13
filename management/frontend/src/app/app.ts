import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { errorMessage } from './core/api/error-message';
import { MeService } from './core/api/me.service';
import { Me } from './core/api/models';
import { AuthService } from './core/auth/auth.service';
import { hasOversight, mayActOnIncidents } from './core/auth/roles';

/** Role slug → what the header calls it. */
const ROLE_LABELS: Record<string, string> = {
  'global-admin': 'Global administrator',
  'it-steuerung': 'IT Steuerung',
  'it-security': 'IT Security',
};

/** Role slug → what the role may do, for the chip's tooltip. */
const ROLE_EXPLANATIONS: Record<string, string> = {
  'global-admin': 'Sees every use case and is the only role that may price a model.',
  'it-steuerung': 'Sees every use case and the whole spend report, and may change none of it.',
  'it-security': 'Sees every use case for security oversight — its configuration, not its content.',
};

@Component({
  selector: 'app-root',
  // `RouterLinkActive` must be imported: an attribute that matches no directive is silently inert,
  // and the navigation could not say which area you were in.
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private readonly meService = inject(MeService);
  private readonly auth = inject(AuthService);

  protected readonly title = signal('AIRA Gateway');
  protected readonly me = signal<Me | null>(null);

  /** The issuer the console could not reach at startup, or `null` (`AuthService.startupError`). */
  protected readonly startupError = this.auth.startupError;

  /** Why signing in again stopped being tried, or `null` (`AuthService.reauthenticate`). */
  protected readonly loginLoop = this.auth.loginLoop;

  /**
   * Why the console could not load your account, or `null` when it could.
   *
   * Everything role-shaped in the shell — the username, the role chips, Logout, the incident and
   * oversight navigation — comes from `/me`, so a failure that removes controls has to say so. This
   * is the shell: there is no page below it to carry the message.
   */
  protected readonly accountError = signal<string | null>(null);

  ngOnInit(): void {
    if (this.auth.isAuthenticated()) {
      this.meService.get().subscribe({
        next: (me) => {
          this.me.set(me);
          this.accountError.set(null);
        },
        error: (error: unknown) =>
          this.accountError.set(errorMessage(error, 'Your account could not be loaded.')),
      });
    }
  }

  /** A full reload rather than a retry: what failed happened before the application existed, and
   *  re-running one step would leave the rest of the startup half-done. */
  protected retryStartup(): void {
    window.location.reload();
  }

  /** Whether this caller may see the security console — **seeing**, not acting (`FRD-206`). */
  protected hasOversight(): boolean {
    return hasOversight(this.me()?.roles);
  }

  /**
   * May this person act on an incident — and therefore read what was actually sent? Narrower than
   * {@link hasOversight} by exactly `it-steuerung`, which sees every figure and no content
   * (`FRD-505`).
   */
  protected mayInvestigate(): boolean {
    return mayActOnIncidents(this.me()?.roles);
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
   * One chip per role the token carries: the slug for machines, the words for people. Which role is
   * asking is the first half of "why can I not do this".
   */
  protected roleChips(): { slug: string; label: string; explains: string }[] {
    return (this.me()?.roles ?? [])
      .filter((slug) => slug in ROLE_LABELS)
      .map((slug) => ({
        slug,
        label: ROLE_LABELS[slug],
        explains: ROLE_EXPLANATIONS[slug] ?? '',
      }));
  }

  protected logout(): void {
    this.auth.logout();
  }

  /** Leave properly — end the session at the provider, the only way out of a login loop. */
  protected signOutCompletely(): void {
    this.auth.signOutCompletely();
  }
}
