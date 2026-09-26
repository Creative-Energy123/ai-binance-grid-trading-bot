import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Button, Card, Empty, ErrorNote, Stat } from "../components/ui";
import { post } from "../lib/api";
import { num, pct, usd } from "../lib/format";
import type { BacktestResponse, BacktestSegment } from "../lib/types";

const SEGMENT_LABEL: Record<string, string> = {
  train: "Training",
  validation: "Validation",
  out_of_sample: "Out-of-sample",
  full: "Full history",
};

function SegmentCard({ segment }: { segment: BacktestSegment }) {
  const m = segment.metrics || {};
  const merged = segment.equity_curve.map((point, i) => ({
    t: point.t,
    equity: point.equity,
    dd: segment.drawdown_curve[i]?.dd_pct ?? 0,
  }));

  return (
    <Card
      title={SEGMENT_LABEL[segment.segment] || segment.segment}
      subtitle={`${segment.candles} candles · ${m.total_trades ?? 0} trades`}
    >
      {segment.warnings.map((w) => (
        <p key={w} className="mb-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">
          {w}
        </p>
      ))}
      {m.total_trades ? (
        <>
          <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="Net P&L" value={usd(m.net_pnl)} tone="pnl" hint={pct(m.net_pnl_pct)} />
            <Stat label="Win rate" value={pct(m.win_rate, 1)} />
            <Stat label="Profit factor" value={num(m.profit_factor)} />
            <Stat label="Max drawdown" value={pct(m.max_drawdown_pct)} />
            <Stat label="Sharpe" value={num(m.sharpe)} />
            <Stat label="Expectancy" value={`${num(m.expectancy_r)}R`} />
            <Stat label="Avg win / loss" value={`${usd(m.average_win)} / ${usd(m.average_loss)}`} />
            <Stat
              label="Costs"
              value={usd((m.fees || 0) + (m.slippage_cost || 0) + (m.funding_fees || 0), 2)}
              hint={m.costs_as_pct_of_gross ? `${m.costs_as_pct_of_gross}% of gross profit` : undefined}
            />
          </div>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={merged}>
                <CartesianGrid stroke="#25324a" strokeDasharray="3 3" />
                <XAxis dataKey="t" tick={{ fill: "#7b8aa3", fontSize: 10 }} tickFormatter={() => ""} />
                <YAxis yAxisId="eq" tick={{ fill: "#7b8aa3", fontSize: 10 }} width={70} domain={["auto", "auto"]} />
                <YAxis yAxisId="dd" orientation="right" tick={{ fill: "#7b8aa3", fontSize: 10 }} width={45} />
                <Tooltip contentStyle={{ background: "#0f1419", border: "1px solid #25324a", fontSize: 12 }} />
                <Line yAxisId="eq" type="monotone" dataKey="equity" stroke="#3d9b8f" dot={false} strokeWidth={2} />
                <Line yAxisId="dd" type="monotone" dataKey="dd" stroke="#c45c5c" dot={false} strokeWidth={1} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            Assumes {m.assumptions?.taker_fee_pct}% taker fee and {m.assumptions?.slippage_pct}% slippage per
            side, and that a candle touching both the stop and a target filled the stop first.
          </p>
        </>
      ) : (
        <Empty>{m.note || "No trades in this segment."}</Empty>
      )}
    </Card>
  );
}

export default function BacktestTab({ symbols }: { symbols: string[] }) {
  const [symbol, setSymbol] = useState(symbols[0] || "BTC/USDT");
  const [candles, setCandles] = useState(3000);
  const [equity, setEquity] = useState(10000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResponse | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await post<BacktestResponse>("/api/research/backtest", {
          symbol,
          candles,
          starting_equity: equity,
          split: true,
        }),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card title="Backtest" subtitle="Train / validation / out-of-sample split on 1m Binance history">
        <ErrorNote message={error} />
        <div className="flex flex-wrap items-end gap-3 text-xs">
          <label className="flex flex-col gap-1">
            <span className="text-slate-400">Symbol</span>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="w-36 rounded border border-slate-700 bg-ink/60 px-2 py-1 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-slate-400">Candles (1m)</span>
            <input
              type="number"
              value={candles}
              min={600}
              max={20000}
              onChange={(e) => setCandles(Number(e.target.value))}
              className="w-28 rounded border border-slate-700 bg-ink/60 px-2 py-1 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-slate-400">Starting equity</span>
            <input
              type="number"
              value={equity}
              onChange={(e) => setEquity(Number(e.target.value))}
              className="w-32 rounded border border-slate-700 bg-ink/60 px-2 py-1 font-mono"
            />
          </label>
          <Button tone="primary" onClick={run} disabled={busy}>
            {busy ? "Running…" : "Run backtest"}
          </Button>
        </div>
      </Card>

      {result && (
        <>
          {result.overfitting_warning && (
            <p className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              <Badge tone="warn">Overfitting check</Badge> {result.overfitting_warning}
            </p>
          )}
          {result.segments.map((s) => (
            <SegmentCard key={s.segment} segment={s} />
          ))}
          <p className="text-[11px] text-slate-500">{result.disclaimer}</p>
        </>
      )}
    </div>
  );
}
