import { useState } from "react";
import { clearTokens, post } from "../lib/api";
import { when } from "../lib/format";
import type { BotStatus, Health } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Badge, Button, ErrorNote } from "../components/ui";
import OverviewTab from "../tabs/OverviewTab";
import LiveTab from "../tabs/LiveTab";
import ScannerTab from "../tabs/ScannerTab";
import JournalTab from "../tabs/JournalTab";
import BacktestTab from "../tabs/BacktestTab";
import AssistantTab from "../tabs/AssistantTab";
import SettingsTab from "../tabs/SettingsTab";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "live", label: "Live Trading" },
  { id: "scanner", label: "Scanner" },
  { id: "journal", label: "Journal" },
  { id: "backtest", label: "Research" },
  { id: "assistant", label: "AI Assistant" },
  { id: "settings", label: "Settings" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Dashboard() {
  const [tab, setTab] = useState<TabId>("overview");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const status = usePoll<BotStatus>("/api/bot/status", 5000);
  const health = usePoll<Health>("/api/health", 15000);
  const bot = status.data;

  async function act(path: string, body?: unknown, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setActionError(null);
    try {
      await post(path, body);
      await status.reload();
      await health.reload();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const modeTone = bot?.mode === "live" ? "bad" : bot?.mode === "testnet" ? "warn" : "info";
  const runTone = bot?.emergency_stopped ? "bad" : bot?.paused ? "warn" : bot?.running ? "ok" : "muted";
  const runLabel = bot?.emergency_stopped
    ? "EMERGENCY STOPPED"
    : bot?.paused
      ? "PAUSED"
      : bot?.running
        ? "RUNNING"
        : "STOPPED";

  return (
    <div className="min-h-screen text-slate-100">
      <header className="border-b border-slate-800 bg-ink/70 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3">
          <div className="mr-auto">
            <h1 className="text-base font-semibold">Adaptive Scalping Bot</h1>
            <p className="text-xs text-slate-400">
              {bot?.status_message || "…"} · last tick {when(bot?.last_tick_at)}
            </p>
          </div>
          <Badge tone={modeTone}>{(bot?.mode || "…").toUpperCase()}</Badge>
          <Badge tone={runTone}>{runLabel}</Badge>
          {bot?.places_real_orders && <Badge tone="bad">REAL ORDERS</Badge>}
          <Button onClick={() => { clearTokens(); location.href = "/login"; }}>Sign out</Button>
        </div>
      </header>

      <div className="border-b border-slate-800 bg-ink/40 px-4 py-2">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2">
          <Button tone="primary" disabled={busy || bot?.running} onClick={() => act("/api/bot/start")}>
            Start bot
          </Button>
          <Button tone="warn" disabled={busy || !bot?.running} onClick={() => act("/api/bot/pause")}>
            Pause new trades
          </Button>
          <Button disabled={busy || !bot?.running} onClick={() => act("/api/bot/stop")}>
            Stop
          </Button>
          <Button
            tone="warn"
            disabled={busy}
            onClick={() => act("/api/bot/close-all", undefined, "Close every open position at market?")}
          >
            Close all positions
          </Button>
          <Button
            tone="danger"
            disabled={busy}
            onClick={() =>
              act(
                "/api/bot/emergency-stop",
                { close_positions: true },
                "EMERGENCY STOP: cancel all orders and flatten every position?",
              )
            }
          >
            Emergency stop
          </Button>
          <div className="ml-auto flex items-center gap-2 text-[11px] text-slate-400">
            {health.data?.components.map((c) => (
              <span key={c.name} title={c.detail}>
                <Badge tone={c.status === "OK" ? "ok" : "bad"}>
                  {c.name} {c.status}
                </Badge>
              </span>
            ))}
          </div>
        </div>
      </div>

      <nav className="border-b border-slate-800 bg-ink/20 px-4">
        <div className="mx-auto flex max-w-7xl gap-1 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`whitespace-nowrap border-b-2 px-3 py-2 text-xs transition ${
                tab === t.id
                  ? "border-accent text-emerald-300"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-7xl px-4 py-4">
        <ErrorNote message={actionError || status.error} />
        {bot?.mode === "live" && (
          <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
            Live mode places real orders with real money. Losses are real and no configuration
            guarantees a profit. Verify the go-live checklist before starting the bot.
          </p>
        )}
        {tab === "overview" && <OverviewTab bot={bot} />}
        {tab === "live" && <LiveTab />}
        {tab === "scanner" && <ScannerTab />}
        {tab === "journal" && <JournalTab />}
        {tab === "backtest" && <BacktestTab symbols={bot?.symbols || []} />}
        {tab === "assistant" && <AssistantTab symbols={bot?.symbols || []} />}
        {tab === "settings" && <SettingsTab bot={bot} onChanged={status.reload} />}
      </main>

      <footer className="px-4 pb-8 pt-2 text-center text-[11px] text-slate-600">
        Backtested and simulated results are not evidence of future returns.
      </footer>
    </div>
  );
}
