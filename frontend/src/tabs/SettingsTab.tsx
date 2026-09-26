import { useEffect, useState } from "react";
import { Badge, Button, Card, ErrorNote } from "../components/ui";
import { api, post } from "../lib/api";
import type { BotStatus } from "../lib/types";
import { usePoll } from "../lib/usePoll";

const RISK_FIELDS: { key: string; label: string; step?: number }[] = [
  { key: "risk_per_trade_pct", label: "Risk per trade (%)", step: 0.05 },
  { key: "max_daily_loss_pct", label: "Max daily loss (%)", step: 0.5 },
  { key: "max_weekly_loss_pct", label: "Max weekly loss (%)", step: 0.5 },
  { key: "max_positions", label: "Max simultaneous positions", step: 1 },
  { key: "max_consecutive_losses", label: "Max consecutive losses", step: 1 },
  { key: "max_trades_per_hour", label: "Max trades per hour", step: 1 },
  { key: "minimum_signal_score", label: "Minimum signal score", step: 1 },
  { key: "futures_leverage", label: "Futures leverage", step: 1 },
];

const MODES = ["paper", "testnet", "live"] as const;

export default function SettingsTab({ bot, onChanged }: { bot: BotStatus | null; onChanged: () => void }) {
  const config = usePoll<Record<string, any>>("/api/bot/risk-config", 30000);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmPhrase, setConfirmPhrase] = useState("");
  const [creds, setCreds] = useState({ api_key: "", api_secret: "", market: "spot", environment: "testnet" });

  useEffect(() => {
    if (config.data && Object.keys(draft).length === 0) {
      const initial: Record<string, number> = {};
      RISK_FIELDS.forEach((f) => (initial[f.key] = config.data![f.key]));
      setDraft(initial);
    }
  }, [config.data]);

  async function run(fn: () => Promise<unknown>, message: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(message);
      onChanged();
      config.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="space-y-4">
      <ErrorNote message={error} />
      {notice && (
        <p className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
          {notice}
        </p>
      )}

      <Card title="Trading mode" subtitle="Paper and testnet never touch real funds">
        <div className="flex flex-wrap items-center gap-2">
          {MODES.map((m) => (
            <Button
              key={m}
              tone={bot?.mode === m ? "primary" : m === "live" ? "danger" : "default"}
              onClick={() =>
                run(() => post("/api/bot/mode", { mode: m }), `Mode switched to ${m}`)
              }
            >
              {m.toUpperCase()}
            </Button>
          ))}
          <Badge tone={bot?.live_confirmed ? "ok" : "muted"}>
            {bot?.live_confirmed ? "Live confirmed" : "Live not confirmed"}
          </Badge>
        </div>
        <div className="mt-3 rounded border border-rose-500/30 bg-rose-500/5 p-3">
          <p className="text-xs text-rose-200">
            Before live mode: complete the go-live checklist in the docs. Confirmation validates the keys
            and refuses any key that has withdrawal permission enabled.
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

      <Card title="Risk configuration" subtitle="Applied immediately to the running engine">
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          {RISK_FIELDS.map((f) => (
            <label key={f.key} className="flex flex-col gap-1 text-xs">
              <span className="text-slate-400">{f.label}</span>
              <input
                type="number"
                step={f.step}
                value={draft[f.key] ?? ""}
                onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })}
                className="rounded border border-slate-700 bg-ink/60 px-2 py-1 font-mono"
              />
            </label>
          ))}
        </div>
        <div className="mt-3">
          <Button
            tone="primary"
            onClick={() => run(() => post("/api/bot/risk-config", draft), "Risk configuration updated")}
          >
            Save risk configuration
          </Button>
        </div>
        {config.data && (
          <pre className="mt-3 max-h-48 overflow-auto rounded bg-ink/60 p-2 text-[10px] text-slate-400">
            {JSON.stringify(config.data, null, 2)}
          </pre>
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
