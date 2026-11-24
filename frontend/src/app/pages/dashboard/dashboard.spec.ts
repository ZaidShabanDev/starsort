import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { Me, TrialStatus } from '../../starsort-api';
import { Dashboard } from './dashboard';
import { RunPanel } from './run-panel/run-panel';

const READY: TrialStatus = { unlimited: false, can_run: true, reason: null, retry_at: null, message: null };

function user(trial: Partial<TrialStatus> = {}): Me {
  return { login: 'octo', id: 1, name: 'Octo Cat', avatar_url: 'https://a', trial: { ...READY, ...trial } };
}

describe('Dashboard', () => {
  let http: HttpTestingController;
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [Dashboard],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
  });

  afterEach(() => http.verify());

  let fixture: ComponentFixture<Dashboard>;

  async function render(answer: (request: TestRequest) => void): Promise<HTMLElement> {
    fixture = TestBed.createComponent(Dashboard);
    fixture.detectChanges();
    answer(http.expectOne('/api/me'));
    fixture.detectChanges();
    // The run panel appears once the user is loaded and asks for the last run.
    http.match('/api/jobs/latest').forEach((request) => request.flush({ error: 'no runs yet' }, { status: 404, statusText: 'Not Found' }));
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  function banner(page: HTMLElement): HTMLElement {
    return page.querySelector('[role="status"]') as HTMLElement;
  }

  it('shows the user and a ready banner', async () => {
    const page = await render((request) => request.flush(user()));

    expect(page.textContent).toContain('Octo Cat');
    expect(page.textContent).toContain('@octo');
    expect(banner(page).dataset['tone']).toBe('ok');
  });

  it('shows the next run date when the weekly run is used', async () => {
    const page = await render((request) =>
      request.flush(user({ can_run: false, reason: 'weekly', retry_at: 1_800_000_000, message: 'One run per week.' })),
    );

    expect(banner(page).dataset['tone']).toBe('wait');
    expect(banner(page).textContent).toContain('Next free run');
  });

  it('shows the budget message when the trial is paused', async () => {
    const page = await render((request) =>
      request.flush(user({ can_run: false, reason: 'budget', message: 'Paused until next month.' })),
    );

    expect(banner(page).dataset['tone']).toBe('stop');
    expect(banner(page).textContent).toContain('Paused until next month.');
  });

  it('marks unlimited testers', async () => {
    const page = await render((request) => request.flush(user({ unlimited: true })));

    expect(page.textContent).toContain('Tester: no weekly limit');
  });

  it('goes back to the landing page when not signed in', async () => {
    await render((request) => request.flush({ error: 'not signed in' }, { status: 401, statusText: 'Unauthorized' }));

    expect(router.navigateByUrl).toHaveBeenCalledWith('/');
  });

  it('shows an error when the API fails', async () => {
    const page = await render((request) => request.flush({ error: 'boom' }, { status: 500, statusText: 'Server Error' }));

    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Could not load your account');
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  it('shows the run panel once the user is loaded', async () => {
    const page = await render((request) => request.flush(user()));

    expect(page.querySelector('app-run-panel')).not.toBeNull();
  });

  it('reloads the trial status when a run starts or ends', async () => {
    const page = await render((request) => request.flush(user()));

    fixture.debugElement.query(By.directive(RunPanel)).componentInstance.trialChanged.emit();
    http.expectOne('/api/me').flush(user({ can_run: false, reason: 'running', message: 'A job is already running.' }));
    await fixture.whenStable();

    expect(banner(page).dataset['tone']).toBe('wait');
  });

  function button(page: HTMLElement, text: string): HTMLButtonElement {
    return Array.from(page.querySelectorAll('button')).find((element) => element.textContent?.trim() === text) as HTMLButtonElement;
  }

  it('asks before deleting the data, and cancel changes nothing', async () => {
    const page = await render((request) => request.flush(user()));

    button(page, 'Delete my data').click();
    fixture.detectChanges();
    expect(page.textContent).toContain('Your GitHub Lists and stars stay as they are');

    button(page, 'Cancel').click();
    fixture.detectChanges();
    expect(button(page, 'Delete my data')).toBeTruthy();
    http.expectNone({ method: 'DELETE', url: '/api/me/data' });
  });

  it('deletes the data and goes to the landing page', async () => {
    const page = await render((request) => request.flush(user()));

    button(page, 'Delete my data').click();
    fixture.detectChanges();
    button(page, 'Yes, delete').click();
    http.expectOne({ method: 'DELETE', url: '/api/me/data' }).flush({ deleted_jobs: 2 });

    expect(router.navigateByUrl).toHaveBeenCalledWith('/?deleted=1');
  });

  it('shows why the data was not deleted', async () => {
    const page = await render((request) => request.flush(user()));

    button(page, 'Delete my data').click();
    fixture.detectChanges();
    button(page, 'Yes, delete').click();
    http
      .expectOne({ method: 'DELETE', url: '/api/me/data' })
      .flush({ error: 'A job is running. Wait for it to finish, then delete your data.' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();

    expect(page.querySelector('[role="alert"]')?.textContent).toContain('A job is running');
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  it('signs out and returns to the landing page', async () => {
    const page = await render((request) => request.flush(user()));

    (page.querySelector('button.btn--plain') as HTMLButtonElement).click();
    http.expectOne({ method: 'POST', url: '/api/auth/logout' }).flush({ status: 'signed out' });

    expect(router.navigateByUrl).toHaveBeenCalledWith('/');
  });
});
