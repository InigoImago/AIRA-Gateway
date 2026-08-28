import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { errorMessage } from './core/api/error-message';
import { MeService } from './core/api/me.service';
import { Me } from './core/api/models';
import { AuthService } from './core/auth/auth.service';
import { hasOversight, mayActOnIncidents } from './core/auth/roles';

const ROLE_LABELS: Record<string, string> = {
  'global-admin': 'Global administrator',
  'it-steuerung': 'IT Steuerung',
  'it-security': 'IT Security',
};

const ROLE_EXPLANATIONS: Record<string, string> = {
  'global-admin': 'Sees every use case and is the only role that may price a model.',
  'it-steuerung': 'Sees every use case and the whole spend report, and may change none of it.',
  'it-security': 'Sees every use case for security oversight — its configuration, not its content.',
};

@Component({
  selector: 'app-root',
  // `RouterLinkActive` was missing, and Angular does not complain: an attribute that matches no
  // directive is simply inert markup. Every nav item carried `routerLinkActive="is-active"` and
  // none of them ever got the class, so the navigation could not say which area you were in — for
  // as long as the shell has existed. The `is-active` rule in `app.scss` was styling nothing.
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private readonly meService = inject(MeService);
  private readonly auth = inject(AuthService);

  protected readonly title = signal('AIRA Gateway');
  protected readonly me = signal<Me | null>(null);

  /**
   * The issuer the console could not reach at startup, or `null` when it could.
   *
   * Exposed to the template so the shell can say so instead of rendering nothing — see
   * `AuthService.startupError`, and `app.html` for why it replaces the routes rather than sitting
   * above them.
   */
  protected readonly startupError = this.auth.startupError;

  /**
   * Why the console could not load your account, or `null` when it could.
   *
   * `/me` had no error branch, and everything role-shaped in the shell is derived from it: the
   * username, the role chips, **Logout**, and the nav entries for investigating an incident and
   * for oversight. So a failed call left an IT Security reader looking at a console built for
   * somebody with fewer rights — no error, no explanation, and no way to sign out. `FRD-206`'s
   * complaint inverted: a capability with no way in, and only the other kind announces itself.
   *
   * A failure that removes controls has to say so, which is §3's "no silent failures in the UI"
   * at the one place where nothing else can report it — this is the shell, so there is no page
   * below it to carry the message.
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

  /**
   * Reload the page.
   *
   * A full reload rather than a retry of the discovery call: everything that failed happened
   * *before* the application existed, so re-running one step of it would leave the rest of the
   * startup half-done. The identity provider coming back is not the common case here — a person
   * fixing a deployment is — and a page they can press is what tells them it worked.
   */
  protected retryStartup(): void {
    window.location.reload();
  }

  // `hasRole(role: string)` stood here until 2026-08-27 and was called by nothing — not by this
  // component, not by its template, not by any other file. An unreachable helper is a rule the
  // code claims and does not have, and this one claimed the wrong rule twice over: it was the
  // **generic** role check, sitting beside `hasOversight()` and `mayInvestigate()`, which both go
  // through `core/auth/roles.ts`. A contributor reaching for the obvious-looking one would have
  // written the fourth copy of a role list inside the console whose one-definition file exists to
  // prevent the third. Removed rather than wired up: the questions this console asks are the four
  // in `roles.ts`, and "does this person hold an arbitrary role" is not one of them.

  /**
   * Whether this caller may see the security console — **seeing**, not acting.
   *
   * `it-steuerung` belongs here and may stop nothing; the console itself keeps that apart, because
   * conflating "may look" with "may act" is a mistake this project has made once already.
   */
  protected hasOversight(): boolean {
    return hasOversight(this.me()?.roles);
  }

  /**
   * May this person act on an incident — and therefore read what was actually sent?
   *
   * Narrower than {@link hasOversight} by exactly `it-steuerung`, which sees every figure and no
   * content (`FRD-505`). Both predicates come from `core/auth/roles.ts`: this method used to write
   * the role list out by hand, which is the shape of the 2026-08-07 finding where one plane let
   * `it-steuerung` stop traffic and the other refused it.
   */
  protected mayInvestigate(): boolean {
    return mayActOnIncidents(this.me()?.roles);
  }

  /**
   * Whether to offer the pipeline-tests screen (`ADR-0020`).
   *
   * **The server's answer, not a role list.** `MayRunTests` asks an object-level question — do
   * you **administer** anything (owner's rule, 2026-08-16; it asked for membership before that) —
   * and the only fields here that look like an answer are the roles and `use_cases`, which carries
   * the `/use-cases/<slug>` group convention alone. A predicate written from those would hide the
   * screen from everybody who reaches a use case through a *grant*, silently, which is
   * `FRD-206`'s defect inverted.
   *
   * The class was `MayTestModels` and asked for `view_usecase` when this was written, and this
   * sentence went on saying so after both changed. It decides nothing — the field does — which is
   * exactly why it could drift unnoticed: a stale reason beside a correct line is read as the
   * reason, and the next person to touch the gate reasons from it.
   *
   * `?? false` rather than `?? true`: an older backend that does not send the field offers nothing
   * rather than offering an entry that 403s.
   */
  protected mayTest(): boolean {
    return this.me()?.may_test ?? false;
  }

  /**
   * Which hat the signed-in person is wearing, in words rather than in realm slugs.
   *
   * Half of what a role-based console has to answer is "why can I not do this", and the first step
   * is saying which role is asking. `use-case-admin` is a claim name; "Use-Case-Administrator" is
   * an answer.
   */
  /**
   * One chip per role the token carries: the slug for machines, the words for people.
   *
   * This replaced three disabled navigation tabs, one per oversight role, that pointed at screens
   * which do not exist yet. A tab that cannot be clicked teaches nothing except that something is
   * broken — but the *property* those tabs encoded (what the console shows follows the roles in
   * the token) is worth keeping, so it moved here rather than disappearing.
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
}
