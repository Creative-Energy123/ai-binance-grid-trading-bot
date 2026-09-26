import { Badge, Card, Empty, ErrorNote, Table } from "../components/ui";
import { num, pct, pnlClass, price, usd, when } from "../lib/format";
import type { Position, ScanRow, SignalRow } from "../lib/types";
import { usePoll } from "../lib/usePoll";

export default function LiveTab() {
  const positions = usePoll<Position[]>("/api/bot/positions", 4000);
  const scan = usePoll<ScanRow[]>("/api/market/scan", 20000);
  const signals = usePoll<SignalRow[]>("/api/journal/signals?limit=15", 10000);
  const prices = new Map((scan.data || []).map((r) => [r.symbol, r.price]));

  return (
    <div className="space-y-4">
      <ErrorNote message={positions.error} />

      <Card title="Open positions" subtitle="Live stop, targets and distance to liquidation">
        {positions.data?.length ? (
          <Table
            head={[
              "Symbol",
              "Side",
              "Strategy",
              "Entry",
              "Mark",
              "Stop",
              "TP1 / TP2",
              "Qty",
              "Lev",
              "Liq",
              "Risk",
              "P&L",
            ]}
          >
            {positions.data.map((p) => {
              const mark = prices.get(p.symbol) ?? p.entry_price;
              const pnlPct = p.entry_price
                ? ((mark - p.entry_price) / p.entry_price) * 100 * (p.side === "long" ? 1 : -1)
                : 0;
              const liqDistance = p.liquidation_price
                ? Math.abs((mark - p.liquidation_price) / mark) * 100
                : null;
              return (
                <tr key={p.id} className="hover:bg-slate-800/30">
                  <td className="px-2 py-2">{p.symbol}</td>
                  <td className="px-2 py-2">
                    <Badge tone={p.side === "long" ? "ok" : "bad"}>{p.side.toUpperCase()}</Badge>
                  </td>
                  <td className="px-2 py-2 text-slate-400">{p.strategy}</td>
                  <td className="px-2 py-2">{price(p.entry_price)}</td>
                  <td className="px-2 py-2">{price(mark)}</td>
                  <td className="px-2 py-2 text-rose-300">
                    {price(p.stop_price)}
                    {p.tp1_filled && <span className="ml-1 text-[10px] text-emerald-400">BE</span>}
                  </td>
                  <td className="px-2 py-2 text-emerald-300">
                    {price(p.tp1_price)} / {price(p.tp2_price)}
                  </td>
                  <td className="px-2 py-2">{num(p.remaining_quantity, 6)}</td>
                  <td className="px-2 py-2">{p.leverage}x</td>
                  <td className="px-2 py-2">
                    {p.liquidation_price ? (
                      <span className={liqDistance && liqDistance < 10 ? "text-rose-400" : ""}>
                        {price(p.liquidation_price)} ({pct(liqDistance, 1)})
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-2 py-2">{usd(p.risk_amount)}</td>
                  <td className={`px-2 py-2 ${pnlClass(p.unrealized_pnl)}`}>
                    {usd(p.unrealized_pnl)} ({pct(pnlPct)})
                  </td>
                </tr>
              );
            })}
          </Table>
        ) : (
          <Empty>No open positions. Holding cash is a valid state for a regime-aware bot.</Empty>
        )}
      </Card>

      <Card title="Signal feed" subtitle="Everything the signal engine produced, accepted or vetoed">
        {signals.data?.length ? (
          <ul className="space-y-2 text-xs">
            {signals.data.map((s) => (
              <li key={s.id} className="rounded border border-slate-700/50 bg-ink/40 p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={s.accepted ? "ok" : "warn"}>{s.accepted ? "EXECUTED" : "REJECTED"}</Badge>
                  <span className="font-mono text-slate-200">{s.symbol}</span>
                  <Badge tone={s.side === "long" ? "ok" : "bad"}>{s.side.toUpperCase()}</Badge>
                  <span className="text-slate-400">{s.strategy}</span>
                  <span className="font-mono text-slate-300">score {s.score.toFixed(0)}/100</span>
                  <span className="text-slate-500">R:R {s.reward_risk.toFixed(2)}</span>
                  <span className="ml-auto text-[11px] text-slate-500">{when(s.created_at)}</span>
                </div>
                <div className="mt-1 font-mono text-[11px] text-slate-400">
                  entry {price(s.entry_price)} · stop {price(s.stop_price)} · tp1 {price(s.tp1_price)} · tp2{" "}
                  {price(s.tp2_price)}
                </div>
                {!s.accepted && s.rejection_reason && (
                  <p className="mt-1 text-[11px] text-amber-300">Blocked: {s.rejection_reason}</p>
                )}
                {s.reasons?.length > 0 && (
                  <p className="mt-1 text-[11px] text-slate-500">{s.reasons.slice(0, 5).join(" · ")}</p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No signals yet.</Empty>
        )}
      </Card>
    </div>
  );
}
