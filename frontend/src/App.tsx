import { useEffect, useState } from 'react';

type ConnectionState = 'loading' | 'connected' | 'error';

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(
  /\/$/,
  '',
);

function App() {
  const [connectionState, setConnectionState] = useState<ConnectionState>('loading');
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    const checkBackend = async () => {
      setConnectionState('loading');
      setErrorMessage('');

      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/health`);
        if (!response.ok) {
          throw new Error(`Backend returned HTTP ${response.status}`);
        }
        const payload: unknown = await response.json();
        if (
          !payload ||
          typeof payload !== 'object' ||
          !('status' in payload) ||
          payload.status !== 'ok'
        ) {
          throw new Error('Backend returned an unexpected health response');
        }
        setConnectionState('connected');
      } catch (error) {
        setConnectionState('error');
        setErrorMessage(error instanceof Error ? error.message : 'Unable to reach the backend');
      }
    };

    void checkBackend();
  }, []);

  const statusLabel = {
    loading: 'Checking backend…',
    connected: 'Backend connected',
    error: 'Backend unavailable',
  }[connectionState];

  return (
    <main className="app-shell">
      <section className="hero-card" aria-labelledby="app-title">
        <div className="eyebrow">Invoice operations workspace</div>
        <h1 id="app-title">InvoiceFlow AI</h1>
        <p className="intro">
          A focused foundation for reliable invoice processing, validation, and human review.
        </p>

        <div className={`connection-card ${connectionState}`} role="status" aria-live="polite">
          <span className="status-dot" aria-hidden="true" />
          <div>
            <strong>{statusLabel}</strong>
            {connectionState === 'loading' && <p>Contacting the FastAPI service.</p>}
            {connectionState === 'connected' && <p>API health check passed successfully.</p>}
            {connectionState === 'error' && <p>{errorMessage}</p>}
          </div>
        </div>

        <footer>
          <span>API base</span>
          <code>{apiBaseUrl}</code>
        </footer>
      </section>
    </main>
  );
}

export default App;
