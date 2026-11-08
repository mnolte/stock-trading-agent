import { Suspense, lazy } from 'react';
import { BrowserRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { Location } from 'react-router-dom';

import { LiveUpdatesProvider } from './api/live';
import { useAuthStatus } from './api/auth';
import { AskProvider } from './ask/AskContext';
import { Layout } from './components/Layout';
import { ErrorBoundary } from './components/ErrorBoundary';
import { RouteNotFound } from './components/errorPages';
import { SkeletonTable, SkeletonTiles } from './components/skeletons';

const Backtest = lazy(() => import('./pages/Backtest'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Decision = lazy(() => import('./pages/Decision'));
const DevComponents = lazy(() => import('./pages/DevComponents'));
const Insights = lazy(() => import('./pages/Insights'));
const Login = lazy(() => import('./pages/Login'));
const Inspector = lazy(() => import('./pages/Inspector'));
const Logs = lazy(() => import('./pages/Logs'));
const Performance = lazy(() => import('./pages/Performance'));
const Positions = lazy(() => import('./pages/Positions'));
const Readiness = lazy(() => import('./pages/Readiness'));
const Reviews = lazy(() => import('./pages/Reviews'));
const Settings = lazy(() => import('./pages/Settings'));
const Strategies = lazy(() => import('./pages/Strategies'));
const States = lazy(() => import('./pages/States'));
const Universe = lazy(() => import('./pages/Universe'));

function PageFallback() {
  return (
    <div
      role="status"
      aria-label="Loading page"
      style={{
        maxWidth: 'var(--sa-page-max)',
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-5)',
      }}
    >
      <span className="sa-sr">Loading page</span>
      <div className="sa-loading">
        <SkeletonTiles count={4} />
        <SkeletonTable
          columns={[{ label: '' }, { label: '', align: 'right' }, { label: '', align: 'right' }]}
          track="1fr 120px 120px"
          rows={5}
        />
      </div>
    </div>
  );
}

function AppRoutes() {
  const location = useLocation();
  // Unknown routes show the 404 page when signed in — and go to sign-in
  // when not, so nothing is revealed (spec → Not found, a page).
  const authStatus = useAuthStatus();
  const background = (
    location.state as { backgroundLocation?: Location } | null
  )?.backgroundLocation;
  return (
    <>
      <Suspense fallback={<PageFallback />}>
        <Routes location={background ?? location}>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="positions" element={<Positions />} />
            <Route path="performance" element={<Performance />} />
            <Route path="insights" element={<Insights />} />
          <Route path="inspector" element={<Inspector />} />
            <Route path="reviews" element={<Reviews />} />
            <Route path="strategies" element={<Strategies />} />
            <Route path="backtest" element={<Backtest />} />
            <Route path="readiness" element={<Readiness />} />
            {/* Deep links to /decisions/:id draw the dashboard under the drawer. */}
            <Route path="decisions/:id" element={<Dashboard />} />
            <Route path="settings" element={<Settings />} />
            <Route path="universe" element={<Universe />} />
            <Route path="states" element={<States />} />
            <Route path="logs" element={<Logs />} />
            <Route path="dev/components" element={<DevComponents />} />
            <Route
              path="*"
              element={
                <RouteNotFound
                  signedIn={
                    !authStatus?.required || Boolean(authStatus?.signed_in)
                  }
                />
              }
            />
          </Route>
        </Routes>
      </Suspense>
      {/* The decision detail is always the drawer from
          design/screens/Decision.dc.html, overlaid on the page behind it.
          Its own boundary keeps the page behind it from falling back. */}
      <Suspense fallback={null}>
        <Routes>
          <Route
            path="/decisions/:id"
            element={
              <ErrorBoundary>
                <Decision />
              </ErrorBoundary>
            }
          />
          {/* A catch-all keeps this Routes quiet on every other page. */}
          <Route path="*" element={null} />
        </Routes>
      </Suspense>
    </>
  );
}

function App() {
  return (
    <BrowserRouter useTransitions={false}>
      <LiveUpdatesProvider>
        <AskProvider>
          <AppRoutes />
        </AskProvider>
      </LiveUpdatesProvider>
    </BrowserRouter>
  );
}

export default App;
