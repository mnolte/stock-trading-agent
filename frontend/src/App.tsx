import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { Layout } from './components/Layout';
import Dashboard from './pages/Dashboard';
import DevComponents from './pages/DevComponents';
import Inspector from './pages/Inspector';
import Performance from './pages/Performance';
import Positions from './pages/Positions';
import Settings from './pages/Settings';
import States from './pages/States';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="positions" element={<Positions />} />
          <Route path="performance" element={<Performance />} />
          <Route path="inspector" element={<Inspector />} />
          <Route path="settings" element={<Settings />} />
          <Route path="states" element={<States />} />
          <Route path="dev/components" element={<DevComponents />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
