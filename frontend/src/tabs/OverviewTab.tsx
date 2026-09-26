import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, Empty, ErrorNote, Stat } from "../components/ui";
import { num, pct, usd, when } from "../lib/format";
import type { BotStatus, Overview, RiskEvent } from "../lib/types";
import { usePoll } from "../lib/usePoll";

type Performance = {
  equity_curve: { t: string; equity: number }[];
  expectancy_r: number;
  average_win: number;
  average_loss: number;
  fees: number;
  sharpe: number;
};

export default function OverviewTab({ bot }: { bot: BotStatus | null }) {
  const overview = usePoll<Overview>("/api/bot/overview", 5000);
  const perf = usePoll<Performance>("/api/journal/performance", 15000);
  const events = usePoll<RiskEvent[]>("/api/journal/risk-events?limit=12", 20000);
  const o = overview.data;

  return (
    <div className="space-y-4">
      <ErrorNote message={overview.error} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        <Stat label="Equity" value={usd(o?.equity)} hint={`balance ${usd(o?.balance)}`} />
        <Stat label="Available" value={usd(o?.available)} />
        <Stat label="Today P&L" value={usd(o?.today_pnl)} tone="pnl" />
        <Stat label="Week P&L" value={usd(o?.week_pnl)} tone="pnl" />
        <Stat label="Month P&L" value={usd(o?.month_pnl)} tone="pnl" />
        <Stat label="Unrealized" value={usd(o?.unrealized_pnl)} tone="pnl" />
        <Stat label="Win rate" value={pct(o?.win_rate, 1)} hint={`${o?.trades ?? 0} trades`} />
        <Stat label="Profit factor" value={num(o?.profit_factor)} />
        <Stat label="Max drawdown" value={pct(o?.max_drawdown_pct)} />
        <Stat label="Expectancy" value={`${num(perf.data?.expectancy_r)}R`} />
        <Stat label="Fees paid" value={usd(perf.data?.fees, 4)} />
        <Stat label="Open positions" value={String(o?.open_positions ?? 0)} />
      </div>

      <Card title="Equity curve" subtitle="Realized P&L per closed trade, net of fees and slippage">
        {perf.data?.equity_curve?.length ? (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={perf.data.equity_curve}>
                <defs>
                  <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3d9b8f" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="#3d9b8f" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#25324a" strokeDasharray="3 3" />
                <XAxis dataKey="t" tick={{ fill: "#7b8aa3", fontSize: 10 }} tickFormatter={(v) => String(v).slice(5, 16)} />
                <YAxis tick={{ fill: "#7b8aa3", fontSize: 10 }} domain={["auto", "auto"]} width={70} />
                <Tooltip
                  contentStyle={{ background: "#0f1419", border: "1px solid #25324a", fontSize: 12 }}
                  formatter={(v: number) => usd(v)}
                />
                <Area type="monotone" dataKey="equity" stroke="#3d9b8f" fill="url(#eq)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty>No closed trades yet — the curve appears after the first round trip.</Empty>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Bot state">
          <dl className="space-y-1.5 text-xs">
            {[
              ["Mode", bot?.mode],
              ["Market", bot?.futures_enabled ? `USDT-M futures (${bot.leverage}x isolated)` : "Spot"],
              ["Symbols", bot?.symbols.join(", ")],
              ["Consecutive losses", String(bot?.consecutive_losses ?? 0)],
              ["Cooldown until", when(bot?.cooldown_until)],
              ["Peak equity", usd(bot?.peak_equity)],
              ["Realized P&L", usd(bot?.realized_pnl)],
            ].map(([k, v]) => (
              <div key={String(k)} className="flex justify-between gap-4 border-b border-slate-800/60 pb-1">
                <dt className="text-slate-400">{k}</dt>
                <dd className="font-mono text-slate-200">{v || "—"}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card title="Recent risk events" subtitle="Every veto and safeguard the engine fired">
          {events.data?.length ? (
            <ul className="space-y-2 text-xs">
              {events.data.map((e) => (
                <li key={e.id} className="border-b border-slate-800/60 pb-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-slate-300">{e.event_type}</span>
                    <span className="text-[11px] text-slate-500">{when(e.created_at)}</span>
                  </div>
                  <p className="text-slate-400">{e.detail}</p>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No risk events recorded.</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}
