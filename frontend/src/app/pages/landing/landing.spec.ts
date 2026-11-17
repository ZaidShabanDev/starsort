import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Landing } from './landing';

describe('Landing', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [Landing],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  async function render(signedIn: boolean): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(Landing);
    fixture.detectChanges();
    const request = http.expectOne('/api/me');
    if (signedIn) {
      request.flush({ login: 'octo' });
    } else {
      request.flush({ error: 'not signed in' }, { status: 401, statusText: 'Unauthorized' });
    }
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  function links(page: HTMLElement): (string | null)[] {
    return Array.from(page.querySelectorAll('a')).map((link) => link.getAttribute('href'));
  }

  it('offers GitHub sign-in when signed out', async () => {
    const page = await render(false);

    expect(links(page)).toContain('/api/auth/login');
    expect(links(page)).not.toContain('/dashboard');
  });

  it('links to the dashboard when already signed in', async () => {
    const page = await render(true);

    expect(links(page)).toContain('/dashboard');
    expect(links(page)).not.toContain('/api/auth/login');
  });

  it('shows the free trial rules', async () => {
    const page = await render(false);

    expect(page.textContent).toContain('1 run per week');
    expect(page.textContent).toContain('500 newest');
  });
});
