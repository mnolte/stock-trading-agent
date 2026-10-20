import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import type { Location } from 'react-router-dom';

import { LiveUpdatesProvider } from './api/live';
import { Layout } from './components/Layout';
import Dashboard from './pages/Dashboard';
import Decision from './pages/Decision';
import DevComponents from './pages/DevComponents';
import Inspector from './pages/Inspector';
import Performance from './pages/Performance';
import Positions from './pages/Positions';
import Reviews from './pages/Reviews';
import Settings from './pages/Settings';
import Strategies from './pages/Strategies';
import States from './pages/States';
import Universe from './pages/Universe';

function AppRoutes() {
  const location = useLocation();
  const background = (
    location.state as { backgroundLocation?: Location } | null
  )?.backgroundLocation;
  return (
    <>
      <Routes location={background ?? location}>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="positions" element={<Positions />} />
          <Route path="performance" element={<Performance />} />
          <Route path="inspector" element={<Inspector />} />
          <Route path="reviews" element={<Reviews />} />
          <Route path="strategies" element={<Strategies />} />
          {/* Deep links to /decisions/:id draw the dashboard under the drawer. */}
          <Route path="decisions/:id" element={<Dashboard />} />
          <Route path="settings" element={<Settings />} />
          <Route path="universe" element={<Universe />} />
          <Route path="states" element={<States />} />
          <Route path="dev/components" element={<DevComponents />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      {/* The decision detail is always the drawer from
          design/screens/Decision.dc.html, overlaid on the page behind it. */}
      <Routes>
        <Route path="/decisions/:id" element={<Decision />} />
      </Routes>
    </>
  );
}

function App() {
  return (
    <BrowserRouter>
      <LiveUpdatesProvider>
        <AppRoutes />
      </LiveUpdatesProvider>
    </BrowserRouter>
  );
}

export default App;
