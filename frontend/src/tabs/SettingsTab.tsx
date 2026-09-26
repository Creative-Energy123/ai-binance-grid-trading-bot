import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, ErrorNote } from "../components/ui";
import { api, post } from "../lib/api";
import type { BotStatus } from "../lib/types";
import { usePoll } from "../lib/usePoll";

type Field =
  | { key: string; label: string; type: "number"; step?: number; hint?: string }
  | { key: string; label: string; type: "bool"; hint?: string }
  | { key: string; label: string; type: "text"; hint?: string }
  | { key: string; label: string; type: "select"; options: string[]; hint?: string };

type Group = { title: string; hint?: string; fields: Field[] };

type Integration = {
  name: string;
  label: string;
  configured: boolean;
  source: string;
  masked: string;
  updated_at: string | null;
};

type ConfigResponse = {
  values: Record<string, any>;
  meta: {
    timeframes: string[];
    modes: string[];
    stop_modes: string[];
    requires_flat: string[];
    round_trip_cost_pct: number;
    normalised_weights: Record<string, number>;
  };
};

const MODES = ["backtest", "paper", "testnet", "live"];

function groups(meta: ConfigResponse["meta"]): Group[] {
  return [
    {
      title: "Symbols and timeframes",
      hint: "Changing these requires no open positions. Entry < setup < primary.",
      fields: [
        { key: "symbols", label: "Symbols (comma separated)", type: "text" },
        { key: "timeframe_primary", label: "Primary (direction)", type: "select", options: meta.timeframes },
        { key: "timeframe_setup", label: "Setup", type: "select", options: meta.timeframes },
        { key: "timeframe_entry", label: "Entry (timing)", type: "select", options: meta.timeframes },
      ],
    },
    {
      title: "Risk",
      hint: "Risk per trade is the real control. Exposure caps only limit concentration.",
      fields: [
        { key: "risk_per_trade_pct", label: "Risk per trade (%)", type: "number", step: 0.05 },
        { key: "max_daily_loss_pct", label: "Max daily loss (%)", type: "number", step: 0.5 },
        { key: "max_weekly_loss_pct", label: "Max weekly loss (%)", type: "number", step: 0.5 },
        { key: "max_positions", label: "Max simultaneous positions", type: "number", step: 1 },
        { key: "max_consecutive_losses", label: "Max consecutive losses", type: "number", step: 1 },
        { key: "max_trades_per_hour", label: "Max trades per hour", type: "number", step: 1 },
        { key: "max_symbol_exposure_pct", label: "Max per-symbol exposure (%)", type: "number", step: 5 },
        { key: "max_total_exposure_pct", label: "Max total exposure (%)", type: "number", step: 5 },
        { key: "loss_cooldown_minutes", label: "Cooldown after a loss (min)", type: "number", step: 5 },
      ],
    },
    {
      title: "Strategies",
      hint: "The regime engine still decides which of these may run at any moment.",
      fields: [
        { key: "strategy_trend_pullback", label: "Trend pullback", type: "bool" },
        { key: "strategy_breakout", label: "Breakout", type: "bool" },
        { key: "strategy_range_scalping", label: "Range scalping", type: "bool" },
      ],
    },
    {
      title: "Signal scoring",
      hint: "Weights are normalised to 100, so the threshold keeps its meaning.",
      fields: [
        { key: "minimum_signal_score", label: "Minimum score to trade", type: "number", step: 1 },
        { key: "weight_trend", label: "Weight — trend", type: "number", step: 1 },
        { key: "weight_momentum", label: "Weight — momentum", type: "number", step: 1 },
        { key: "weight_volume", label: "Weight — volume", type: "number", step: 1 },
        { key: "weight_volatility", label: "Weight — volatility", type: "number", step: 1 },
        { key: "weight_structure", label: "Weight — support/resistance", type: "number", step: 1 },
        { key: "weight_entry", label: "Weight — entry timing", type: "number", step: 1 },
      ],
    },
    {
      title: "Stops and targets",
      fields: [
        { key: "stop_mode", label: "Stop mode", type: "select", options: meta.stop_modes },
        { key: "atr_stop_multiplier", label: "ATR stop multiplier", type: "number", step: 0.1 },
        { key: "fixed_stop_pct", label: "Fixed stop (%)", type: "number", step: 0.1 },
        { key: "structure_lookback", label: "Structure lookback (candles)", type: "number", step: 1 },
        { key: "tp1_r_multiple", label: "TP1 (R multiple)", type: "number", step: 0.1 },
        { key: "tp2_r_multiple", label: "TP2 (R multiple)", type: "number", step: 0.1 },
        { key: "tp1_close_fraction", label: "Fraction closed at TP1", type: "number", step: 0.05 },
        { key: "move_stop_to_breakeven_after_tp1", label: "Breakeven stop after TP1", type: "bool" },
        { key: "trailing_enabled", label: "Trailing stop after TP1", type: "bool" },
        { key: "trailing_atr_multiplier", label: "Trailing ATR multiplier", type: "number", step: 0.1 },
      ],
    },
    {
      title: "Costs",
      hint: `Round trip currently assumed at ${meta.round_trip_cost_pct.toFixed(3)}%. Set these to your real VIP tier and measured slippage — signals are gated on reward/risk after costs.`,
      fields: [
        { key: "taker_fee_pct", label: "Taker fee per side (%)", type: "number", step: 0.005 },
        { key: "maker_fee_pct", label: "Maker fee per side (%)", type: "number", step: 0.005 },
        { key: "slippage_pct", label: "Assumed slippage per side (%)", type: "number", step: 0.005 },
      ],
    },
    {
      title: "Futures",
      hint: "Requires no open positions. Shorting is only possible with futures enabled.",
      fields: [
        { key: "futures_enabled", label: "USDT-M futures", type: "bool" },
        { key: "futures_leverage", label: "Leverage", type: "number", step: 1 },
        { key: "futures_max_leverage", label: "Leverage hard cap", type: "number", step: 1 },
        { key: "min_liquidation_distance_pct", label: "Min liquidation distance (%)", type: "number", step: 1 },
      ],
    },
    {
      title: "Runtime",
      fields: [
        { key: "engine_interval_seconds", label: "Engine tick (seconds)", type: "number", step: 5 },
        { key: "market_data_staleness_seconds", label: "Market data staleness (seconds)", type: "number", step: 30 },
        { key: "starting_paper_equity_usdt", label: "Paper starting equity (USDT)", type: "number", step: 500 },
        { key: "ai_enabled", label: "AI assistant enabled", type: "bool" },
      ],
    },
  ];
}

export default function SettingsTab({ bot, onChanged }: { bot: BotStatus | null; onChanged: () => void }) {
  const config = usePoll<ConfigResponse>("/api/bot/config", 0);
  const integrations = usePoll<Integration[]>("/api/account/integrations", 0);
  const [secretDraft, setSecretDraft] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmPhrase, setConfirmPhrase] = useState("");
  const [creds, setCreds] = useState({ api_key: "", api_secret: "", market: "spot", environment: "testnet" });

  useEffect(() => {
    if (config.data) setDraft(config.data.values);
  }, [config.data]);

  const dirty = useMemo(() => {
    if (!config.data) return {};
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(draft)) {
      if (config.data.values[k] !== v) out[k] = v;
    }
    return out;
  }, [draft, config.data]);
  const dirtyCount = Object.keys(dirty).length;

  async function run(fn: () => Promise<unknown>, message: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(message);
      onChanged();
      await config.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function field(f: Field) {
    const value = draft[f.key];
    const changed = config.data && config.data.values[f.key] !== value;
    const ring = changed ? "border-amber-500/60" : "border-slate-700";

    if (f.type === "bool") {
      return (
        <label key={f.key} className="flex items-center gap-2 text-xs" title={f.hint}>
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => setDraft({ ...draft, [f.key]: e.target.checked })}
            className="h-4 w-4 accent-emerald-500"
          />
          <span className={changed ? "text-amber-300" : "text-slate-300"}>{f.label}</span>
        </label>
      );
    }

    return (
      <label key={f.key} className="flex flex-col gap-1 text-xs" title={f.hint}>
        <span className={changed ? "text-amber-300" : "text-slate-400"}>{f.label}</span>
        {f.type === "select" ? (
          <select
            value={value ?? ""}
            onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
            className={`rounded border bg-ink/60 px-2 py-1 ${ring}`}
          >
            {f.options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        ) : (
          <input
            type={f.type === "number" ? "number" : "text"}
            step={f.type === "number" ? f.step : undefined}
            value={value ?? ""}
            onChange={(e) =>
              setDraft({
                ...draft,
                [f.key]: f.type === "number" ? Number(e.target.value) : e.target.value,
              })
            }
            className={`rounded border bg-ink/60 px-2 py-1 font-mono ${ring}`}
          />
        )}
      </label>
    );
  }

  return (
    <div className="space-y-4">
      <ErrorNote message={error || config.error} />
      {notice && (
        <p className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
          {notice}
        </p>
      )}

      <Card title="Trading mode" subtitle="Backtest and paper never place an order; testnet uses Binance testnet funds">
        <div className="flex flex-wrap items-center gap-2">
          {MODES.map((m) => (
            <Button
              key={m}
              tone={bot?.mode === m ? "primary" : m === "live" ? "danger" : "default"}
              onClick={() => run(() => post("/api/bot/mode", { mode: m }), `Mode switched to ${m}`)}
            >
              {m.toUpperCase()}
            </Button>
          ))}
          <Badge tone={bot?.live_confirmed ? "ok" : "muted"}>
            {bot?.live_confirmed ? "Live confirmed" : "Live not confirmed"}
          </Badge>
          {bot?.places_real_orders && <Badge tone="bad">Places real orders</Badge>}
        </div>
        <div className="mt-3 rounded border border-rose-500/30 bg-rose-500/5 p-3">
          <p className="text-xs text-rose-200">
            Live mode needs this confirmation. It validates the API keys and is refused if the key has
            withdrawal permission enabled. Work through the go-live checklist first.
          </p>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <input
              value={confirmPhrase}
              onChange={(e) => setConfirmPhrase(e.target.value)}
              placeholder="ENABLE LIVE TRADING"
              className="w-56 rounded border border-slate-700 bg-ink/60 px-2 py-1 text-xs font-mono"
            />
            <Button
              tone="danger"
              onClick={() =>
                run(
                  () => post("/api/bot/confirm-live", { confirm_phrase: confirmPhrase, acknowledge_risk: true }),
                  "Live trading confirmed — the bot is still stopped",
                )
              }
            >
              Confirm live trading
            </Button>
          </div>
        </div>
      </Card>

      {config.data && (
        <>
          <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-slate-700/60 bg-ink/90 px-3 py-2 backdrop-blur">
            <span className="text-xs text-slate-400">
              {dirtyCount ? `${dirtyCount} unsaved change(s)` : "No unsaved changes"}
            </span>
            <div className="ml-auto flex gap-2">
              <Button disabled={!dirtyCount} onClick={() => setDraft(config.data!.values)}>
                Discard
              </Button>
              <Button
                tone="primary"
                disabled={!dirtyCount}
                onClick={() => run(() => post("/api/bot/config", dirty), "Configuration applied")}
              >
                Save configuration
              </Button>
              <Button
                tone="warn"
                onClick={() =>
                  window.confirm("Discard all overrides and fall back to the deployed environment values?") &&
                  run(() => post("/api/bot/config/reset"), "Configuration reset to deployment defaults")
                }
              >
                Reset to deployed defaults
              </Button>
            </div>
          </div>

          {groups(config.data.meta).map((group) => (
            <Card key={group.title} title={group.title} subtitle={group.hint}>
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">{group.fields.map(field)}</div>
            </Card>
          ))}
        </>
      )}

      <Card
        title="API keys and integrations"
        subtitle="Encrypted with AES-256-GCM at rest. Saved values are never returned — only a masked fragment."
      >
        {integrations.data?.length ? (
          <div className="space-y-2">
            {integrations.data.map((row) => (
              <div
                key={row.name}
                className="flex flex-wrap items-center gap-2 rounded border border-slate-700/50 bg-ink/40 p-2"
              >
                <div className="min-w-[220px]">
                  <div className="text-xs text-slate-200">{row.label}</div>
                  <div className="text-[11px] text-slate-500">
                    {row.configured ? (
                      <>
                        <Badge tone={row.source === "database" ? "ok" : "info"}>{row.source}</Badge>{" "}
                        {row.masked}
                      </>
                    ) : (
                      <Badge tone="muted">not set</Badge>
                    )}
                  </div>
                </div>
                <input
                  type="password"
                  placeholder={row.configured ? "Replace value…" : "Enter value…"}
                  value={secretDraft[row.name] ?? ""}
                  onChange={(e) => setSecretDraft({ ...secretDraft, [row.name]: e.target.value })}
                  className="flex-1 rounded border border-slate-700 bg-ink/60 px-2 py-1 font-mono text-xs"
                />
                <Button
                  disabled={!secretDraft[row.name]}
                  onClick={() =>
                    run(async () => {
                      await post("/api/account/integrations", {
                        name: row.name,
                        value: secretDraft[row.name],
                      });
                      setSecretDraft({ ...secretDraft, [row.name]: "" });
                      await integrations.reload();
                    }, `${row.label} saved`)
                  }
                >
                  Save
                </Button>
                <Button
                  tone="warn"
                  disabled={row.source !== "database"}
                  onClick={() =>
                    run(async () => {
                      await api(`/api/account/integrations/${row.name}`, { method: "DELETE" });
                      await integrations.reload();
                    }, `${row.label} cleared`)
                  }
                >
                  Clear
                </Button>
              </div>
            ))}
            <p className="text-[11px] text-slate-500">
              "environment" means the value comes from the deployment's env vars. Saving here stores it
              in the database instead, which takes effect immediately and survives restarts.
            </p>
          </div>
        ) : (
          <p className="text-xs text-slate-500">Loading integrations…</p>
        )}
      </Card>

      <Card
        title="Binance API credentials"
        subtitle="Encrypted with AES-256-GCM at rest; never returned by any endpoint"
      >
        <p className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          Never enable withdrawal permission on the API key used by this bot. Enable trading only and
          restrict the key to this server's IP address.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-slate-400">API key</span>
            <input
              value={creds.api_key}
              onChange={(e) => setCreds({ ...creds, api_key: e.target.value })}
              className="rounded border border-slate-700 bg-ink/60 px-2 py-1 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-slate-400">API secret</span>
            <input
              type="password"
              value={creds.api_secret}
              onChange={(e) => setCreds({ ...creds, api_secret: e.target.value })}
              className="rounded border border-slate-700 bg-ink/60 px-2 py-1 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-slate-400">Market</span>
            <select
              value={creds.market}
              onChange={(e) => setCreds({ ...creds, market: e.target.value })}
              className="rounded border border-slate-700 bg-ink/60 px-2 py-1"
            >
              <option value="spot">Spot</option>
              <option value="futures">USDT-M futures</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-slate-400">Environment</span>
            <select
              value={creds.environment}
              onChange={(e) => setCreds({ ...creds, environment: e.target.value })}
              className="rounded border border-slate-700 bg-ink/60 px-2 py-1"
            >
              <option value="testnet">Testnet</option>
              <option value="live">Live</option>
            </select>
          </label>
        </div>
        <div className="mt-3">
          <Button
            onClick={() =>
              run(async () => {
                await api("/api/account/credentials", { method: "POST", body: JSON.stringify(creds) });
                setCreds({ ...creds, api_key: "", api_secret: "" });
              }, "Credentials stored and validated")
            }
          >
            Save credentials
          </Button>
        </div>
      </Card>
    </div>
  );
}
