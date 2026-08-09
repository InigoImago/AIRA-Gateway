import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
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

  ngOnInit(): void {
    if (this.auth.isAuthenticated()) {
      this.meService.get().subscribe((me) => this.me.set(me));
    }
  }

  protected hasRole(role: string): boolean {
    return this.me()?.roles.includes(role) ?? false;
  }

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
