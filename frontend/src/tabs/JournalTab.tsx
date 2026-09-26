import { Badge, Button, Card, Empty, ErrorNote, Table } from "../components/ui";
import { getToken } from "../lib/api";
import { duration, num, pct, pnlClass, price, usd, when } from "../lib/format";
import type { Trade } from "../lib/types";
import { usePoll } from "../lib/usePoll";

async function downloadCsv() {
  const res = await fetch("/api/journal/trades.csv", {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "trade-journal.csv";
  link.click();
  URL.revokeObjectURL(url);
}

export default function JournalTab() {
  const trades = usePoll<Trade[]>("/api/journal/trades?limit=300", 15000);

  return (
    <Card
      title="Trade journal"
      subtitle="Every closed round trip, with the costs that produced the net number"
      actions={<Button onClick={downloadCsv}>Export CSV</Button>}
    >
      <ErrorNote message={trades.error} />
      {trades.data?.length ? (
        <Table
          head={[
            "Closed",
            "Symbol",
            "Side",
            "Strategy",
            "Regime",
            "Score",
            "Entry",
            "Exit",
            "Qty",
            "Fees",
            "Slip",
            "P&L",
            "R",
            "Held",
            "Exit reason",
          ]}
        >
          {trades.data.map((t) => (
            <tr key={t.id} className="hover:bg-slate-800/30">
              <td className="px-2 py-2 text-slate-400">{when(t.closed_at)}</td>
              <td className="px-2 py-2">{t.symbol}</td>
              <td className="px-2 py-2">
                <Badge tone={t.side === "long" ? "ok" : "bad"}>{t.side.toUpperCase()}</Badge>
              </td>
              <td className="px-2 py-2 text-slate-400">{t.strategy}</td>
              <td className="px-2 py-2 text-slate-400">{t.regime}</td>
              <td className="px-2 py-2">{num(t.signal_score, 0)}</td>
              <td className="px-2 py-2">{price(t.entry_price)}</td>
              <td className="px-2 py-2">{price(t.exit_price)}</td>
              <td className="px-2 py-2">{num(t.quantity, 6)}</td>
              <td className="px-2 py-2 text-slate-400">{usd(t.fees, 4)}</td>
              <td className="px-2 py-2 text-slate-400">{usd(t.slippage, 4)}</td>
              <td className={`px-2 py-2 ${pnlClass(t.pnl)}`}>
                {usd(t.pnl)} <span className="text-[10px]">({pct(t.pnl_pct)})</span>
              </td>
              <td className={`px-2 py-2 ${pnlClass(t.r_multiple)}`}>{num(t.r_multiple)}R</td>
              <td className="px-2 py-2 text-slate-400">{duration(t.duration_seconds)}</td>
              <td className="px-2 py-2 text-slate-400">{t.exit_reason}</td>
            </tr>
          ))}
        </Table>
      ) : (
        <Empty>No closed trades yet.</Empty>
      )}
    </Card>
  );
}
