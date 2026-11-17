import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Me, TrialStatus } from '../../starsort-api';
import { Dashboard } from './dashboard';

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

  async function render(answer: (request: TestRequest) => void): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(Dashboard);
    fixture.detectChanges();
    answer(http.expectOne('/api/me'));
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

  it('signs out and returns to the landing page', async () => {
    const page = await render((request) => request.flush(user()));

    (page.querySelector('button.btn--plain') as HTMLButtonElement).click();
    http.expectOne({ method: 'POST', url: '/api/auth/logout' }).flush({ status: 'signed out' });

    expect(router.navigateByUrl).toHaveBeenCalledWith('/');
  });
});
