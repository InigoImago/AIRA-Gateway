import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { UseCaseService } from '../../core/api/use-case.service';
import { UseCaseList } from './use-case-list';

describe('UseCaseList', () => {
  it('renders the use cases returned by the service', async () => {
    TestBed.configureTestingModule({
      imports: [UseCaseList],
      providers: [
        provideRouter([]),
        {
          provide: UseCaseService,
          useValue: {
            list: () =>
              of([{ slug: 'demo-uc', name: 'Demo', description: '', processing_notes: '' }]),
          },
        },
      ],
    });

    const fixture = TestBed.createComponent(UseCaseList);
    fixture.detectChanges();
    await fixture.whenStable();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Demo');
    expect(text).toContain('demo-uc');
  });
});
