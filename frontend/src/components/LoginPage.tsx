import { useEffect, useState, type FormEvent } from "react";
import { Lock } from "lucide-react";
import { api } from "../lib/api";

interface LoginPageProps {
  accessControlEnabled?: boolean;
  onSuccess: () => void | Promise<void>;
}

export function LoginPage({ accessControlEnabled = false, onSuccess }: LoginPageProps) {
  const [mode, setMode] = useState<"account" | "token">(
    accessControlEnabled ? "account" : "token",
  );
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!accessControlEnabled) setMode("token");
  }, [accessControlEnabled]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.login(
        mode === "token" ? { token } : { username: username.trim(), password },
      );
      await onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-screen flex items-center justify-center bg-surface-0">
      <div className="w-80">
        <div className="flex flex-col items-center mb-6">
          <div className="h-10 w-10 rounded-lg bg-accent/10 flex items-center justify-center mb-3">
            <Lock className="h-5 w-5 text-accent" />
          </div>
          <h1 className="text-lg font-semibold text-gray-100">
            CloakBrowser Manager
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            {mode === "account" ? "Sign in to your assigned browser sandboxes" : "Enter your administrator access token"}
          </p>
        </div>
        <form onSubmit={handleSubmit}>
          {mode === "account" ? (
            <>
              <label className="label" htmlFor="login-username">Username</label>
              <input
                id="login-username"
                type="text"
                className="input mb-3"
                placeholder="your-name"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
              />
              <label className="label" htmlFor="login-password">Password</label>
              <input
                id="login-password"
                type="password"
                className="input mb-3"
                placeholder="Your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </>
          ) : (
            <input
              type="password"
              className="input mb-3"
              placeholder="Access token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="current-password"
              autoFocus
            />
          )}
          {error && <p className="text-red-400 text-xs mb-3">{error}</p>}
          <button
            type="submit"
            disabled={loading || (mode === "token" ? !token : !username.trim() || !password)}
            className="btn-primary w-full disabled:opacity-50"
          >
            {loading ? "Authenticating..." : mode === "account" ? "Sign in" : "Unlock"}
          </button>
        </form>
        {accessControlEnabled && (
          <button
            type="button"
            onClick={() => {
              setError(null);
              setMode((current) => current === "account" ? "token" : "account");
            }}
            className="mt-3 w-full text-xs text-gray-500 underline hover:text-gray-300"
          >
            {mode === "account" ? "Use an administrator token" : "Sign in with a user account"}
          </button>
        )}
      </div>
    </div>
  );
}
