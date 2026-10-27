import React, { useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { ApiError } from '../utils/api';
import PasswordInput from './PasswordInput';
import { focusRing } from './ui';

const Login: React.FC = () => {
  const { user, login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get('next') ?? '/library';
  // Only same-app paths, so ?next= cannot bounce the user to another site.
  const safeNext = next.startsWith('/') && !next.startsWith('//') ? next : '/library';

  if (user) {
    return <Navigate to={safeNext} replace />;
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      navigate(safeNext, { replace: true });
    } catch (caught) {
      console.error('login failed', caught);
      setError(caught instanceof ApiError ? caught.message : 'Backend tidak bisa dihubungi');
    } finally {
      setIsSubmitting(false);
    }
  };

  const inputClass = `w-full min-h-[44px] px-3 border border-gray-300 rounded-md text-gray-900 ${focusRing}`;

  return (
    <div className="max-w-md mx-auto bg-white p-8 rounded-lg shadow-md">
      <h2 className="text-2xl font-bold mb-2">Login</h2>
      <p className="text-gray-700 mb-6">Login untuk download post dan buka Library.</p>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div>
          <label htmlFor="login-username" className="block text-sm font-semibold text-gray-700 mb-1">
            Username
          </label>
          <input
            id="login-username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            className={inputClass}
            required
          />
        </div>
        <div>
          <label htmlFor="login-password" className="block text-sm font-semibold text-gray-700 mb-1">
            Password
          </label>
          <PasswordInput
            id="login-password"
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
          />
        </div>
        {error && (
          <p role="alert" className="text-red-700">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={isSubmitting}
          className={`min-h-[44px] rounded-md bg-gray-900 text-white font-semibold hover:bg-gray-700 disabled:opacity-60 ${focusRing}`}
        >
          {isSubmitting ? 'Masuk...' : 'Masuk'}
        </button>
      </form>
    </div>
  );
};

export default Login;
