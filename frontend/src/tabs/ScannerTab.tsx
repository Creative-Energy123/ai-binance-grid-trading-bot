import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Table } from "../components/ui";
import { api } from "../lib/api";
import { num, pct, price, regimeLabel } from "../lib/format";
import type { ScanRow } from "../lib/types";
import { usePoll } from "../lib/usePoll";

const REGIME_TONE: Record<string, string> = {
  strong_bull: "ok",
  weak_bull: "ok",
  strong_bear: "bad",
  weak_bear: "bad",
  ranging: "info",
  high_volatility: "warn",
  low_volatility: "muted",
  unclear: "warn",
};

export default function ScannerTab() {
  const scan = usePoll<ScanRow[]>("/api/market/scan", 20000);
  const [detail, setDetail] = useState<any | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  async function inspect(symbol: string) {
    setDetailError(null);
    try {
      setDetail(await api(`/api/market/analysis/${encodeURIComponent(symbol)}`));
    } catch (err) {
      setDetailError((err as Error).message);
    }
  }

  return (
    <div className="space-y-4">
      <ErrorNote message={scan.error || detailError} />

      <Card
        title="Market scanner"
        subtitle="Regime, enabled strategies and liquidity for every configured symbol"
        actions={<Button onClick={scan.reload}>Refresh</Button>}
      >
        {scan.data?.length ? (
          <Table
            head={["Symbol", "Price", "Regime", "Signal", "Score", "RSI", "ADX", "ATR%", "Vol×", "Spread", "Strategies", ""]}
          >
            {scan.data.map((r) => (
              <tr key={r.symbol} className="hover:bg-slate-800/30">
                <td className="px-2 py-2">{r.symbol}</td>
                <td className="px-2 py-2">{price(r.price)}</td>
                <td className="px-2 py-2">
                  <Badge tone={REGIME_TONE[r.regime || ""] || "muted"}>{regimeLabel(r.regime)}</Badge>
                </td>
                <td className="px-2 py-2">
                  {r.signal ? (
                    <Badge tone={r.signal === "long" ? "ok" : "bad"}>{r.signal.toUpperCase()}</Badge>
                  ) : (
                    <span className="text-slate-500">none</span>
                  )}
                </td>
                <td className="px-2 py-2">{num(r.score, 0)}</td>
                <td className="px-2 py-2">{num(r.rsi, 0)}</td>
                <td className="px-2 py-2">{num(r.adx, 0)}</td>
                <td className="px-2 py-2">{num(r.atr_pct, 2)}</td>
                <td className="px-2 py-2">{num(r.volume_ratio, 2)}</td>
                <td className={`px-2 py-2 ${r.liquid === false ? "text-rose-400" : ""}`}>
                  {pct(r.spread_pct, 3)}
                </td>
                <td className="px-2 py-2">
                  <div className="flex gap-1">
                    {Object.entries(r.strategies_enabled || {}).map(([k, v]) => (
                      <span
                        key={k}
                        title={`${k}: ${v ? "enabled" : "disabled"}`}
                        className={`text-[10px] ${v ? "text-emerald-400" : "text-slate-600"}`}
                      >
                        {k.slice(0, 2).toUpperCase()}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-2 py-2">
                  <Button onClick={() => inspect(r.symbol)}>Why?</Button>
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>{scan.loading ? "Scanning…" : "No scanner data."}</Empty>
        )}
        {scan.data?.some((r) => r.rejected_reason) && (
          <ul className="mt-3 space-y-1 text-[11px] text-slate-400">
            {scan.data
              .filter((r) => r.rejected_reason)
              .map((r) => (
                <li key={r.symbol}>
                  <span className="font-mono text-slate-300">{r.symbol}</span>: not trading —{" "}
                  {r.rejected_reason}
                </li>
              ))}
          </ul>
        )}
      </Card>

      {detail && (
        <Card
          title={`Decision trace — ${detail.symbol}`}
          subtitle="Exactly what the engine saw and decided"
          actions={<Button onClick={() => setDetail(null)}>Close</Button>}
        >
          <div className="grid gap-3 lg:grid-cols-2">
            <div>
              <h3 className="mb-1 text-xs uppercase text-slate-400">Regime</h3>
              <p className="text-xs text-slate-200">{regimeLabel(detail.regime.regime)}</p>
              <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-400">
                {detail.regime.reasons.map((reason: string, i: number) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] text-slate-400">
                Tradable: {String(detail.regime.tradable)}
                {detail.regime.block_reason ? ` (${detail.regime.block_reason})` : ""}
              </p>
            </div>
            <div>
              <h3 className="mb-1 text-xs uppercase text-slate-400">Evaluation</h3>
              <p className="text-xs text-slate-200">
                Best score {num(detail.evaluation.best_score, 1)} / threshold{" "}
                {num(detail.evaluation.minimum_score, 0)}
              </p>
              {detail.evaluation.rejected_reason && (
                <p className="mt-1 text-[11px] text-amber-300">
                  No trade: {detail.evaluation.rejected_reason}
                </p>
              )}
              {detail.evaluation.signal && (
                <pre className="mt-2 max-h-56 overflow-auto rounded bg-ink/60 p-2 text-[10px] text-slate-300">
                  {JSON.stringify(detail.evaluation.signal, null, 2)}
                </pre>
              )}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
