import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Panel } from './ui';

const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, isChecking } = useAuth();
  const location = useLocation();

  if (isChecking) {
    return (
      <Panel role="status">
        <p>Cek sesi login...</p>
      </Panel>
    );
  }
  if (!user) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <>{children}</>;
};

export default RequireAuth;
