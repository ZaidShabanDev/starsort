import { Routes } from '@angular/router';
import { Dashboard } from './pages/dashboard/dashboard';
import { Landing } from './pages/landing/landing';
import { Privacy } from './pages/privacy/privacy';

export const routes: Routes = [
  { path: '', component: Landing, title: 'Starsort' },
  { path: 'dashboard', component: Dashboard, title: 'Dashboard | Starsort' },
  { path: 'privacy', component: Privacy, title: 'Privacy | Starsort' },
  { path: '**', redirectTo: '' },
];
