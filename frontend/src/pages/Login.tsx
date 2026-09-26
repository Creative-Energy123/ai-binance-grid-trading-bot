import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../lib/api";

export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await login(email, password);
      nav("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md bg-panel/80 border border-white/10 rounded-2xl p-8 shadow-xl"
      >
        <p className="text-accent text-sm font-medium tracking-wide mb-1">
          Binance
        </p>
        <h1 className="font-display text-2xl font-bold mb-6">Adaptive Scalping Bot</h1>
        <label className="block text-sm mb-1 text-slate-300">Email</label>
        <input
          className="w-full mb-4 rounded-lg bg-ink border border-white/10 px-3 py-2"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label className="block text-sm mb-1 text-slate-300">Password</label>
        <input
          className="w-full mb-4 rounded-lg bg-ink border border-white/10 px-3 py-2"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && <p className="text-danger text-sm mb-3">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-accent text-ink font-semibold py-2.5 hover:opacity-90 disabled:opacity-50"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-xs text-slate-500 mt-4">
          Trading risks real money. Stay in paper and testnet mode until backtests, paper runs and the go-live checklist all pass.
        </p>
      </form>
    </div>
  );
}
