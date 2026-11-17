import { DOCUMENT, Injectable, effect, inject, signal } from '@angular/core';

export type ThemeChoice = 'system' | 'light' | 'dark';

export const THEME_STORAGE_KEY = 'starsort-theme';

function savedChoice(): ThemeChoice {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    return saved === 'light' || saved === 'dark' ? saved : 'system';
  } catch {
    return 'system'; // storage can be blocked (private mode, strict settings)
  }
}

/** Light, dark, or follow the system. index.html applies the saved choice before Angular starts, so there is no flash. */
@Injectable({ providedIn: 'root' })
export class Theme {
  private readonly document = inject(DOCUMENT);

  readonly choice = signal<ThemeChoice>(savedChoice());

  constructor() {
    effect(() => {
      const choice = this.choice();
      const root = this.document.documentElement;
      if (choice === 'system') {
        delete root.dataset['theme'];
      } else {
        root.dataset['theme'] = choice;
      }
      try {
        if (choice === 'system') {
          localStorage.removeItem(THEME_STORAGE_KEY);
        } else {
          localStorage.setItem(THEME_STORAGE_KEY, choice);
        }
      } catch {
        // not saved, but the page still switches
      }
    });
  }
}
