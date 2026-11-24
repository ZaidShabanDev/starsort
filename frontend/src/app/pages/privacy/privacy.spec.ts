import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Privacy } from './privacy';

describe('Privacy', () => {
  async function render(): Promise<HTMLElement> {
    TestBed.configureTestingModule({ imports: [Privacy], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(Privacy);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('says what is kept, for how long, and that the token is never stored', async () => {
    const page = await render();

    expect(page.textContent).toContain('deleted after 30 days');
    expect(page.textContent).toContain('deleted after 14 days');
    expect(page.textContent).toContain('What it never keeps');
  });

  it('explains how to delete the data and remove access', async () => {
    const page = await render();

    const links = Array.from(page.querySelectorAll('a')).map((link) => link.getAttribute('href'));
    expect(links).toContain('/dashboard');
    expect(links).toContain('https://github.com/settings/applications');
  });
});
