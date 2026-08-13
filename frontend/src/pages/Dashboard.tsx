import { FormEvent, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, clearToken } from "../lib/api";

type Status = {
  running: boolean;
  killed: boolean;
  live_confirmed: boolean;
  testnet: boolean;
  last_price: number | null;
  realized_pnl: number;
  unrealized_pnl: number;
  daily_pnl: number;
  status_message: string;
  max_capital_usdt: number;
  symbol: string | null;
};

type Config = {
  symbol: string;
  lower_price: number;
  upper_price: number;
  grid_count: number;
  grid_type: string;
  capital_usdt: number;
  take_profit_pct: number;
  stop_loss_pct: number;
  max_drawdown_pct: number;
  daily_loss_limit_usdt: number;
};

type Trade = {
  id: number;
  side: string;
  price: number;
  quantity: number;
  pnl: number;
  created_at: string;
};

type AiRow = {
  id: number;
  trend: string;
  volatility_regime: string;
  risk_warnings: string;
  suggested_lower: number | null;
  suggested_upper: number | null;
  approved: boolean;
  applied: boolean;
  created_at: string;
};

const emptyConfig: Config = {
  symbol: "BTC/USDT",
  lower_price: 90000,
  upper_price: 110000,
  grid_count: 10,
  grid_type: "arithmetic",
  capital_usdt: 100,
  take_profit_pct: 0.5,
  stop_loss_pct: 5,
  max_drawdown_pct: 10,
  daily_loss_limit_usdt: 50,
};

export default function Dashboard() {
  const nav = useNavigate();
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Config>(emptyConfig);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [ai, setAi] = useState<AiRow[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [s, c, t, a] = await Promise.all([
        api<Status>("/api/status"),
        api<Config | null>("/api/config"),
        api<Trade[]>("/api/trades?limit=30"),
        api<AiRow[]>("/api/ai?limit=10"),
      ]);
      setStatus(s);
      if (c) setConfig(c);
      setTrades(t);
      setAi(a);
      setError("");
    } catch (err) {
      if (err instanceof Error && err.message === "Unauthorized") {
        nav("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load");
    }
  }, [nav]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  async function post(path: string, body?: unknown) {
    setMsg("");
    setError("");
    try {
      await api(path, {
        method: "POST",
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      setMsg("OK");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    }
  }

  async function saveConfig(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/config", { method: "PUT", body: JSON.stringify(config) });
      setMsg("Config saved");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  function logout() {
    clearToken();
    nav("/login");
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4 mb-8">
        <div>
          <p className="text-accent text-sm">trade.creativeenergy.pk</p>
          <h1 className="text-3xl font-bold font-display">AI Grid Trading Bot</h1>
          <p className="text-slate-400 text-sm mt-1">
            Advisory AI only — grid orders require your start / kill controls.
          </p>
        </div>
        <div className="flex gap-2">
          <span
            className={`px-3 py-1 rounded-full text-xs font-mono ${
              status?.testnet ? "bg-warn/20 text-warn" : "bg-danger/20 text-danger"
            }`}
          >
            {status?.testnet ? "TESTNET" : "LIVE"}
          </span>
          <button onClick={logout} className="text-sm text-slate-400 hover:text-white">
            Log out
          </button>
        </div>
      </header>

      {(error || msg) && (
        <div
          className={`mb-4 rounded-lg px-4 py-2 text-sm ${
            error ? "bg-danger/20 text-danger" : "bg-accent/20 text-accent"
          }`}
        >
          {error || msg}
        </div>
      )}

      <section className="grid md:grid-cols-4 gap-4 mb-8">
        {[
          ["Status", status?.status_message ?? "—"],
          ["Last price", status?.last_price?.toFixed(2) ?? "—"],
          ["Realized PnL", status?.realized_pnl?.toFixed(4) ?? "—"],
          ["Daily PnL", status?.daily_pnl?.toFixed(4) ?? "—"],
        ].map(([label, value]) => (
          <div key={label} className="bg-panel/70 border border-white/10 rounded-xl p-4">
            <p className="text-xs text-slate-400 uppercase tracking-wide">{label}</p>
            <p className="font-mono text-lg mt-1">{value}</p>
          </div>
        ))}
      </section>

      <div className="flex flex-wrap gap-2 mb-8">
        <button
          onClick={() => post("/api/bot/start")}
          className="bg-accent text-ink font-semibold px-4 py-2 rounded-lg"
        >
          Start
        </button>
        <button
          onClick={() => post("/api/bot/stop")}
          className="bg-white/10 px-4 py-2 rounded-lg"
        >
          Stop
        </button>
        <button
          onClick={() => post("/api/bot/kill")}
          className="bg-danger text-white font-semibold px-4 py-2 rounded-lg"
        >
          Kill switch
        </button>
        <button
          onClick={() => post("/api/bot/clear-kill")}
          className="bg-white/10 px-4 py-2 rounded-lg"
        >
          Clear kill
        </button>
        <button
          onClick={() => post("/api/ai/run")}
          className="bg-white/10 px-4 py-2 rounded-lg"
        >
          Run AI analysis
        </button>
        {!status?.testnet && !status?.live_confirmed && (
          <button
            onClick={() =>
              post("/api/bot/confirm-live", { confirm_phrase: "ENABLE LIVE TRADING" })
            }
            className="bg-warn text-ink font-semibold px-4 py-2 rounded-lg"
          >
            Confirm live trading
          </button>
        )}
      </div>

      <div className="grid lg:grid-cols-2 gap-6 mb-8">
        <form onSubmit={saveConfig} className="bg-panel/70 border border-white/10 rounded-xl p-5 space-y-3">
          <h2 className="font-semibold text-lg mb-2">Grid configuration</h2>
          {(
            [
              ["symbol", "Symbol"],
              ["lower_price", "Lower price"],
              ["upper_price", "Upper price"],
              ["grid_count", "Grid count"],
              ["capital_usdt", "Capital USDT"],
              ["max_drawdown_pct", "Max drawdown %"],
              ["daily_loss_limit_usdt", "Daily loss limit"],
            ] as const
          ).map(([key, label]) => (
            <div key={key}>
              <label className="text-xs text-slate-400">{label}</label>
              <input
                className="w-full mt-1 rounded-lg bg-ink border border-white/10 px-3 py-2 font-mono text-sm"
                value={String(config[key])}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    [key]:
                      key === "symbol"
                        ? e.target.value
                        : key === "grid_count"
                          ? Number(e.target.value)
                          : Number(e.target.value),
                  })
                }
              />
            </div>
          ))}
          <select
            className="w-full rounded-lg bg-ink border border-white/10 px-3 py-2"
            value={config.grid_type}
            onChange={(e) => setConfig({ ...config, grid_type: e.target.value })}
          >
            <option value="arithmetic">Arithmetic</option>
            <option value="geometric">Geometric</option>
          </select>
          <p className="text-xs text-slate-500">
            Hard capital cap: {status?.max_capital_usdt ?? "—"} USDT
          </p>
          <button type="submit" className="bg-accent text-ink font-semibold px-4 py-2 rounded-lg">
            Save config
          </button>
        </form>

        <div className="bg-panel/70 border border-white/10 rounded-xl p-5">
          <h2 className="font-semibold text-lg mb-3">AI analysis (advisory)</h2>
          <ul className="space-y-3 max-h-96 overflow-auto">
            {ai.length === 0 && <li className="text-slate-500 text-sm">No analyses yet.</li>}
            {ai.map((row) => (
              <li key={row.id} className="border border-white/5 rounded-lg p-3 text-sm">
                <p className="font-mono text-xs text-slate-400">{row.created_at}</p>
                <p>
                  <span className="text-accent">{row.trend}</span> · {row.volatility_regime}
                </p>
                <p className="text-warn mt-1">{row.risk_warnings}</p>
                {!row.applied && (
                  <button
                    className="mt-2 text-xs underline text-slate-300"
                    onClick={() => post(`/api/ai/${row.id}/apply`)}
                  >
                    Apply suggestion
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="bg-panel/70 border border-white/10 rounded-xl p-5">
        <h2 className="font-semibold text-lg mb-3">Recent trades</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono">
            <thead className="text-slate-400 text-left">
              <tr>
                <th className="py-2">Side</th>
                <th>Price</th>
                <th>Qty</th>
                <th>PnL</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} className="border-t border-white/5">
                  <td className="py-2">{t.side}</td>
                  <td>{t.price}</td>
                  <td>{t.quantity}</td>
                  <td>{t.pnl}</td>
                  <td>{new Date(t.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
