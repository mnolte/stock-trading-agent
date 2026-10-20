import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

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
import States from './pages/States';
import Universe from './pages/Universe';

function App() {
  return (
    <BrowserRouter>
      <LiveUpdatesProvider>
        <Routes>
          <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="positions" element={<Positions />} />
          <Route path="performance" element={<Performance />} />
          <Route path="inspector" element={<Inspector />} />
          <Route path="reviews" element={<Reviews />} />
          <Route path="decisions/:id" element={<Decision />} />
          <Route path="settings" element={<Settings />} />
          <Route path="universe" element={<Universe />} />
          <Route path="states" element={<States />} />
          <Route path="dev/components" element={<DevComponents />} />
          <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </LiveUpdatesProvider>
    </BrowserRouter>
  );
}

export default App;
