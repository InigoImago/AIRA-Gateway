import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { InstallationClock } from '../ui/clock';
import { MeService } from './me.service';

const ME = { subject: 's', username: 'demo', email: '', roles: ['global-admin'], use_cases: [] };

function setup() {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [provideHttpClient(), provideHttpClientTesting(), MeService],
  });
  return { service: TestBed.inject(MeService), http: TestBed.inject(HttpTestingController) };
}

describe('MeService', () => {
  it('reads the signed-in profile from the management API', () => {
    const { service, http } = setup();

    let roles: string[] = [];
    service.get().subscribe((me) => (roles = me.roles));
    http.expectOne('/api/v1/me').flush(ME);

    expect(roles).toEqual(['global-admin']);
    expect(service.me()?.username).toBe('demo');
    http.verify();
  });

  it('sets the installation’s clock from the answer, for every screen that shows a time', () => {
    const { service, http } = setup();
    const clock = TestBed.inject(InstallationClock);

    expect(clock.zone()).toBe('UTC');
    service.get().subscribe();
    http.expectOne('/api/v1/me').flush({ ...ME, time_zone: 'Europe/Berlin' });

    expect(clock.zone()).toBe('Europe/Berlin');
    http.verify();
  });

  it('asks once for the shell and every screen, so they cannot disagree', () => {
    // Eleven screens fetched it for themselves: a transient failure on one hid its controls while
    // the shell, which had its answer, showed them — two answers to "what may I do" on one page.
    const { service, http } = setup();
    const seen: string[] = [];

    service.get().subscribe((me) => seen.push(`shell:${me.username}`));
    service.get().subscribe((me) => seen.push(`page:${me.username}`));
    http.expectOne('/api/v1/me').flush(ME);
    service.get().subscribe((me) => seen.push(`later:${me.username}`));

    http.verify();
    expect(seen).toEqual(['shell:demo', 'page:demo', 'later:demo']);
  });

  it('asks again after a failure, rather than keeping it', () => {
    const { service, http } = setup();
    let failed = false;
    service.get().subscribe({ error: () => (failed = true) });
    http.expectOne('/api/v1/me').flush(null, { status: 503, statusText: 'Unavailable' });
    expect(failed).toBe(true);

    let name = '';
    service.get().subscribe((me) => (name = me.username));
    http.expectOne('/api/v1/me').flush(ME);

    expect(name).toBe('demo');
  });

  it('asks again when told to', () => {
    const { service, http } = setup();
    service.get().subscribe();
    http.expectOne('/api/v1/me').flush(ME);

    service.reload().subscribe();
    http.expectOne('/api/v1/me').flush({ ...ME, username: 'renamed' });

    expect(service.me()?.username).toBe('renamed');
  });
});
