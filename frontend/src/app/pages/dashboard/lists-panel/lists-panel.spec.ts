import { Location } from '@angular/common';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { POLL_MS } from '../../../job-polling';
import { Job } from '../../../starsort-api';
import { ListsPanel } from './lists-panel';

const NOT_FOUND = { status: 404, statusText: 'Not Found' };

function sync(fields: Partial<Job> = {}): Job {
  return {
    job_id: 's1',
    kind: 'lists',
    login: 'octo',
    status: 'running',
    created_at: 1_800_000_000,
    updated_at: 1_800_000_000,
    batches_done: 0,
    batches_total: 0,
    ...fields,
  };
}

describe('ListsPanel', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<ListsPanel>;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      imports: [ListsPanel],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  /** TestBed swaps the browser's address bar for a fake one, so the URL is set through Location, like the component reads it. */
  function render(latest?: Job, query = ''): HTMLElement {
    TestBed.inject(Location).go('/dashboard', query);
    fixture = TestBed.createComponent(ListsPanel);
    fixture.detectChanges();
    const request = http.expectOne('/api/jobs/latest?kind=lists');
    if (latest) {
      request.flush(latest);
    } else {
      request.flush({ error: 'no runs yet' }, NOT_FOUND);
    }
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function syncLink(page: HTMLElement): string | null | undefined {
    return page.querySelector('a.btn')?.getAttribute('href');
  }

  it('links to the GitHub approval, asking for private lists only when ticked', () => {
    const page = render();
    expect(syncLink(page)).toBe('/api/auth/lists');

    (page.querySelector('input[type="checkbox"]') as HTMLInputElement).click();
    fixture.detectChanges();

    expect(syncLink(page)).toBe('/api/auth/lists?private=1');
  });

  it('follows a running sync until it is done, cleans the URL and says the sync ended', () => {
    const page = render(sync({ batches_done: 1, batches_total: 4 }), 'lists=started');
    let ended = 0;
    fixture.componentInstance.syncEnded.subscribe(() => ended++);
    expect(page.textContent).toContain('1 of 4 repos');
    expect(TestBed.inject(Location).path()).toBe('/dashboard');

    vi.advanceTimersByTime(POLL_MS);
    http.expectOne('/api/jobs/s1').flush(sync({ status: 'done', lists_created: 2, repos_updated: 4, repos_to_update: 4 }));
    fixture.detectChanges();

    expect(page.textContent).toContain('Created 2 lists and updated 4 repos');
    expect(syncLink(page)).toBe('/api/auth/lists');
    expect(ended).toBe(1);
  });

  it('asks to sync again later when GitHub stopped it early', () => {
    const page = render(sync({ status: 'done', lists_created: 5, repos_updated: 300, repos_to_update: 450 }));

    expect(page.textContent).toContain('Updated 300 of 450 repos');
    expect(page.textContent).toContain('click Sync again in about an hour');
  });

  it('names the organizations whose repos were skipped', () => {
    const page = render(
      sync({ status: 'done', lists_created: 0, repos_updated: 5, repos_to_update: 7, repos_skipped: 2, skipped_orgs: ['awslabs'], complete: true }),
    );

    expect(page.textContent).toContain('updated 5 repos on GitHub');
    expect(page.textContent).toContain('Skipped 2 repos from awslabs');
  });

  it('says when the lists were already up to date', () => {
    const page = render(sync({ status: 'done', lists_created: 0, repos_updated: 0, repos_to_update: 0 }));

    expect(page.textContent).toContain('already match your categories');
  });

  it('explains why the sync did not start', () => {
    const page = render(undefined, 'lists=wrong_account');

    expect(page.querySelector('[role="alert"]')?.textContent).toContain('different GitHub account');
    expect(TestBed.inject(Location).path()).toBe('/dashboard');
  });

  it('shows the error of a failed sync', () => {
    const page = render(sync({ status: 'failed', error: 'GitHub: something broke' }));

    expect(page.querySelector('[role="alert"]')?.textContent).toContain('GitHub: something broke');
  });
});
