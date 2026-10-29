import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { InstallationClock, dayIn, inZone } from './clock';
import { ZonedPipe } from './zoned.pipe';

/**
 * The installation's clock (`FRD-629`). Written in Europe/Berlin, the clock this was asked for,
 * and around the night its clocks go back — the one hour of the year a wall clock shows twice.
 * Every case is an instant in UTC, so the expectations hold whatever zone the test machine is in.
 */
describe('inZone', () => {
  it('prints a moment on the installation’s clock, not on the browser’s', () => {
    // 22:30 UTC on 14 July is 00:30 on the 15th in Berlin, on summer time.
    expect(inZone('2026-07-14T22:30:00Z', 'Europe/Berlin')).toBe('2026-07-15 00:30');
    expect(inZone('2026-07-14T22:30:00Z', 'UTC')).toBe('2026-07-14 22:30');
  });

  it('follows the clock across the change to winter time', () => {
    expect(inZone('2026-10-25T00:30:00Z', 'Europe/Berlin', 'second')).toBe('2026-10-25 02:30:00');
    expect(inZone('2026-10-25T01:30:00Z', 'Europe/Berlin', 'second')).toBe('2026-10-25 02:30:00');
    expect(inZone('2026-10-25T02:30:00Z', 'Europe/Berlin')).toBe('2026-10-25 03:30');
  });

  it('writes midnight as 00, never as 24', () => {
    expect(inZone('2026-10-31T23:00:00Z', 'Europe/Berlin')).toBe('2026-11-01 00:00');
  });

  it('keeps a moment’s date or adds its seconds when asked', () => {
    const moment = '2026-09-30T22:30:15Z';

    expect(inZone(moment, 'Europe/Berlin', 'day')).toBe('2026-10-01');
    expect(inZone(moment, 'Europe/Berlin', 'second')).toBe('2026-10-01 00:30:15');
    expect(inZone(new Date(moment), 'Europe/Berlin')).toBe('2026-10-01 00:30');
  });

  it('shows a calendar date as the day it names', () => {
    // Read as a moment it would be midnight UTC: the 27th anywhere west of Greenwich.
    expect(inZone('2026-10-28', 'America/New_York', 'day')).toBe('2026-10-28');
  });

  it('shows nothing rather than a date nobody can read', () => {
    expect(inZone(null, 'Europe/Berlin')).toBe('');
    expect(inZone(undefined, 'Europe/Berlin')).toBe('');
    expect(inZone('', 'Europe/Berlin')).toBe('');
    expect(inZone('not a time', 'Europe/Berlin')).toBe('');
  });

  it('reads an empty zone as the clock an installation that sets nothing is on', () => {
    expect(inZone('2026-07-14T22:30:00Z', '')).toBe('2026-07-14 22:30');
  });
});

describe('dayIn', () => {
  it('names the installation’s date for a moment', () => {
    expect(dayIn(new Date('2026-09-30T22:30:00Z'), 'Europe/Berlin')).toBe('2026-10-01');
    expect(dayIn(new Date('2026-09-30T22:30:00Z'), 'UTC')).toBe('2026-09-30');
  });
});

@Component({
  imports: [ZonedPipe],
  template: `<span data-testid="when">{{ when | zoned }}</span
    ><span data-testid="day">{{ when | zoned: 'day' }}</span>`,
})
class Host {
  readonly when = '2026-07-14T22:30:00Z';
}

describe('ZonedPipe', () => {
  function render() {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [Host] });
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const text = (testid: string) =>
      (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="${testid}"]`)
        ?.textContent;
    return { fixture, text, clock: TestBed.inject(InstallationClock) };
  }

  it('prints on UTC until the installation’s clock is known', () => {
    const { text } = render();

    expect(text('when')).toBe('2026-07-14 22:30');
    expect(text('day')).toBe('2026-07-14');
  });

  it('follows the clock when `/v1/me` names it, without the value changing', async () => {
    // A pure pipe would keep what it printed for the same value: a timestamp rendered before
    // `/v1/me` answered would stay on UTC.
    const { fixture, text, clock } = render();

    clock.zone.set('Europe/Berlin');
    await fixture.whenStable();
    fixture.detectChanges();

    expect(text('when')).toBe('2026-07-15 00:30');
    expect(text('day')).toBe('2026-07-15');
  });
});
