import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { App } from './app';
import { MeService } from './core/api/me.service';
import { Me } from './core/api/models';
import { AuthService } from './core/auth/auth.service';

const baseMe: Me = {
  subject: 's',
  username: 'demo',
  email: 'demo@x',
  roles: [],
  use_cases: [],
};

function configure(authenticated: boolean, roles: string[] = []): void {
  TestBed.configureTestingModule({
    imports: [App],
    providers: [
      provideRouter([]),
      {
        provide: AuthService,
        useValue: { isAuthenticated: () => authenticated, logout: () => {} },
      },
      { provide: MeService, useValue: { get: () => of({ ...baseMe, roles }) } },
    ],
  });
}

describe('App', () => {
  it('renders the title and use-case navigation', async () => {
    configure(true);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h1')?.textContent).toContain('AIRA Gateway');
    expect(el.textContent).toContain('Use Cases');
    expect(el.querySelector('.aira-user')?.textContent).toContain('demo');
  });

  it('shows role-specific navigation for the user roles', async () => {
    configure(true, ['it-security', 'global-admin']);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-role="it-security"]')).not.toBeNull();
    expect(el.querySelector('[data-role="global-admin"]')).not.toBeNull();
    expect(el.querySelector('[data-role="it-steuerung"]')).toBeNull();
  });

  it('does not load the profile when unauthenticated', async () => {
    configure(false);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).querySelector('.aira-user')).toBeNull();
  });
});
