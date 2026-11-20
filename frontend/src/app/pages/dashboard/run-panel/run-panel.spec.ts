import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Job, TrialStatus } from '../../../starsort-api';
import { POLL_MS, RunPanel } from './run-panel';

const READY: TrialStatus = { unlimited: false, can_run: true, reason: null, retry_at: null, message: null };
const NOT_FOUND = { status: 404, statusText: 'Not Found' };

function job(fields: Partial<Job> = {}): Job {
  return {
    job_id: 'j1',
    login: 'octo',
    status: 'queued',
    created_at: 1_800_000_000,
    updated_at: 1_800_000_000,
    batches_done: 0,
    batches_total: 0,
    ...fields,
  };
}

const DONE = job({
  status: 'done',
  batches_done: 2,
  batches_total: 2,
  markdown: '# Awesome Stars',
  categories: { AI: 3, Web: 5 },
  repo_count: 8,
  new_repo_count: 8,
});

describe('RunPanel', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<RunPanel>;
  let changes: number;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      imports: [RunPanel],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  function render(trial: TrialStatus = READY, latest?: Job): HTMLElement {
    fixture = TestBed.createComponent(RunPanel);
    fixture.componentRef.setInput('trial', trial);
    changes = 0;
    fixture.componentInstance.trialChanged.subscribe(() => changes++);
    fixture.detectChanges();
    const request = http.expectOne('/api/jobs/latest');
    if (latest) {
      request.flush(latest);
    } else {
      request.flush({ error: 'no runs yet' }, NOT_FOUND);
    }
    fixture.detectChanges();
    answerListsPanel();
    return fixture.nativeElement as HTMLElement;
  }

  /** A finished run shows the Lists panel, which asks for the last sync. */
  function answerListsPanel(): void {
    http.match('/api/jobs/latest?kind=lists').forEach((request) => request.flush({ error: 'no runs yet' }, NOT_FOUND));
    fixture.detectChanges();
  }

  function button(page: HTMLElement, text: string): HTMLButtonElement | undefined {
    return Array.from(page.querySelectorAll('button')).find((item) => item.textContent?.includes(text));
  }

  function poll(answer: Job): void {
    vi.advanceTimersByTime(POLL_MS);
    http.expectOne('/api/jobs/j1').flush(answer);
    fixture.detectChanges();
    answerListsPanel();
  }

  it('lets a user start the first run', () => {
    const page = render();

    expect(button(page, 'Start a run')?.disabled).toBe(false);
  });

  it('disables the start button when the trial does not allow a run', () => {
    const page = render({ ...READY, can_run: false, reason: 'weekly' });

    expect(button(page, 'Start a run')?.disabled).toBe(true);
  });

  it('starts a run, shows its progress and then the result', () => {
    const page = render();

    button(page, 'Start a run')?.click();
    http.expectOne({ method: 'POST', url: '/api/jobs' }).flush(job());
    fixture.detectChanges();
    expect(page.textContent).toContain('Waiting to start');
    expect(changes).toBe(1);

    poll(job({ status: 'running', batches_done: 1, batches_total: 2 }));
    expect(page.textContent).toContain('batch 1 of 2');
    expect(page.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('50');

    poll(DONE);
    expect(page.textContent).toContain('8 repos in 2 categories');
    expect(page.querySelector('pre')?.textContent).toContain('# Awesome Stars');
    expect(Array.from(page.querySelectorAll('.chip__name')).map((chip) => chip.textContent)).toEqual(['Web', 'AI']);
    expect(changes).toBe(2);

    vi.advanceTimersByTime(POLL_MS * 3);
    http.expectNone('/api/jobs/j1');
  });

  it('picks up a running job after a page refresh', () => {
    const page = render(READY, job({ status: 'running' }));
    expect(page.textContent).toContain('Reading your stars from GitHub');

    poll(DONE);

    expect(page.querySelector('pre')?.textContent).toContain('# Awesome Stars');
  });

  it('keeps checking when one progress check fails', () => {
    const page = render(READY, job({ status: 'running' }));

    vi.advanceTimersByTime(POLL_MS);
    http.expectOne('/api/jobs/j1').flush({ error: 'boom' }, { status: 502, statusText: 'Bad Gateway' });
    poll(DONE);

    expect(page.querySelector('pre')).not.toBeNull();
  });

  it('shows why a run was refused', () => {
    const page = render();

    button(page, 'Start a run')?.click();
    http
      .expectOne({ method: 'POST', url: '/api/jobs' })
      .flush({ error: 'The free trial allows one run per week.', reason: 'weekly', retry_at: 1 }, { status: 429, statusText: 'Too Many Requests' });
    fixture.detectChanges();

    expect(page.querySelector('[role="alert"]')?.textContent).toContain('one run per week');
    expect(changes).toBe(1);
  });

  it('shows the error of a failed run', () => {
    const page = render(READY, job({ status: 'failed', error: "GitHub user 'octo' was not found." }));

    expect(page.querySelector('[role="alert"]')?.textContent).toContain('was not found');
  });

  it('offers the GitHub Lists sync under a finished run', () => {
    const page = render(READY, DONE);

    expect(page.querySelector('app-lists-panel')).not.toBeNull();
  });

  it('copies the markdown', () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const page = render(READY, DONE);

    button(page, 'Copy Markdown')?.click();

    expect(writeText).toHaveBeenCalledWith('# Awesome Stars');
  });

  it('downloads the markdown as a file', () => {
    URL.createObjectURL = vi.fn(() => 'blob:result');
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const page = render(READY, DONE);

    button(page, 'Download .md')?.click();

    expect(click).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:result');
    click.mockRestore();
  });
});
