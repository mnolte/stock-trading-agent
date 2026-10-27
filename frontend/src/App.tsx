import { Suspense, lazy } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import type { Location } from 'react-router-dom';

import { LiveUpdatesProvider } from './api/live';
import { Layout } from './components/Layout';

const Backtest = lazy(() => import('./pages/Backtest'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Decision = lazy(() => import('./pages/Decision'));
const DevComponents = lazy(() => import('./pages/DevComponents'));
const Insights = lazy(() => import('./pages/Insights'));
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
    <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
      Loading…
    </div>
  );
}

function AppRoutes() {
  const location = useLocation();
  const background = (
    location.state as { backgroundLocation?: Location } | null
  )?.backgroundLocation;
  return (
    <>
      <Suspense fallback={<PageFallback />}>
        <Routes location={background ?? location}>
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
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
      {/* The decision detail is always the drawer from
          design/screens/Decision.dc.html, overlaid on the page behind it.
          Its own boundary keeps the page behind it from falling back. */}
      <Suspense fallback={null}>
        <Routes>
          <Route path="/decisions/:id" element={<Decision />} />
        </Routes>
      </Suspense>
    </>
  );
}

function App() {
  return (
    <BrowserRouter useTransitions={false}>
      <LiveUpdatesProvider>
        <AppRoutes />
      </LiveUpdatesProvider>
    </BrowserRouter>
  );
}

export default App;
