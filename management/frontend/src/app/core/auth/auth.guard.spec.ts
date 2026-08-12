import { signal } from '@angular/core';
import { ActivatedRouteSnapshot, RouterStateSnapshot } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { authGuard } from './auth.guard';
import { AuthService } from './auth.service';

const route = {} as ActivatedRouteSnapshot;
const state = {} as RouterStateSnapshot;

describe('authGuard', () => {
  it('allows the route when authenticated', () => {
    TestBed.configureTestingModule({
      providers: [
        {
          provide: AuthService,
          useValue: {
            isAuthenticated: () => true,
            login: () => {},
            startupError: signal<string | null>(null),
          },
        },
      ],
    });
    const result = TestBed.runInInjectionContext(() => authGuard(route, state));
    expect(result).toBe(true);
  });

  it('starts login and denies when unauthenticated', () => {
    let loginCalled = false;
    TestBed.configureTestingModule({
      providers: [
        {
          provide: AuthService,
          useValue: {
            isAuthenticated: () => false,
            startupError: signal<string | null>(null),
            login: () => {
              loginCalled = true;
            },
          },
        },
      ],
    });
    const result = TestBed.runInInjectionContext(() => authGuard(route, state));
    expect(result).toBe(false);
    expect(loginCalled).toBe(true);
  });

  it('does not start a login it knows cannot work', () => {
    // With the issuer unreachable, `initCodeFlow` navigates the whole page to a host that does
    // not answer — and the reader loses the one thing the console *can* still tell them, which is
    // why it is failing. The shell's explanation has to survive the guard.
    let loginCalled = false;
    TestBed.configureTestingModule({
      providers: [
        {
          provide: AuthService,
          useValue: {
            isAuthenticated: () => false,
            startupError: signal<string | null>('http://localhost:8080/realms/aira'),
            login: () => {
              loginCalled = true;
            },
          },
        },
      ],
    });
    const result = TestBed.runInInjectionContext(() => authGuard(route, state));
    expect(result).toBe(false);
    expect(loginCalled).toBe(false);
  });
});
