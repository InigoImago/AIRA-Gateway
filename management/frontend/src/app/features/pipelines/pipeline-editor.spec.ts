import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ActivatedRoute } from '@angular/router';
import { of } from 'rxjs';
import { PipelineConfig } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PipelineEditor } from './pipeline-editor';

function setup(initial: PipelineConfig) {
  let saved: PipelineConfig | null = null;
  TestBed.configureTestingModule({
    imports: [PipelineEditor],
    providers: [
      provideRouter([]),
      { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => 'demo-uc' } } } },
      {
        provide: UseCaseService,
        useValue: {
          getPipeline: () => of(initial),
          savePipeline: (_slug: string, config: PipelineConfig) => {
            saved = config;
            return of(config);
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(PipelineEditor);
  fixture.detectChanges();
  return { fixture, getSaved: () => saved };
}

describe('PipelineEditor', () => {
  it('renders the graph endpoints for an empty pipeline', () => {
    const { fixture } = setup({ steps: [], fallback_models: [] });
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Request in');
    expect(text).toContain('Dispatch');
  });

  it('adds a step and saves the built pipeline', () => {
    const { fixture, getSaved } = setup({ steps: [], fallback_models: [] });
    const component = fixture.componentInstance as unknown as {
      addStep: (t: string) => void;
      save: () => void;
    };
    component.addStep('injection_filter');
    component.save();
    expect(getSaved()?.steps[0].type).toBe('injection_filter');
    expect(getSaved()?.steps[0].config).toEqual({ mode: 'heuristic', action: 'block' });
  });

  it('renders configured steps from the loaded config', () => {
    const { fixture } = setup({
      steps: [{ type: 'model_route', config: { rules: [{ model: 'cheap-1' }] } }],
      fallback_models: ['backup-1'],
    });
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Model Routing');
    expect(text).toContain('backup-1');
  });
});
