import { useState } from "react";
import { Button, Card, Empty, ErrorNote } from "../components/ui";
import { post } from "../lib/api";
import { when } from "../lib/format";
import type { AiAnalysis } from "../lib/types";
import { usePoll } from "../lib/usePoll";

const SUGGESTIONS = [
  "Why did the bot not take a trade in the last hour?",
  "Explain the current regime for each symbol I am scanning.",
  "Summarise my recent performance and what the losing trades had in common.",
  "Which risk limit am I closest to right now?",
];

export default function AssistantTab({ symbols }: { symbols: string[] }) {
  const history = usePoll<AiAnalysis[]>("/api/ai/history?limit=15", 30000);
  const [question, setQuestion] = useState("");
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(text: string) {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await post("/api/ai/ask", { question: text, symbol: symbol || null });
      setQuestion("");
      await history.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function explain(sym: string) {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/ai/explain/${encodeURIComponent(sym)}`);
      await history.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card
        title="AI assistant"
        subtitle="Explains the bot's own state. It cannot place, size or cancel orders, and it does not predict outcomes."
      >
        <ErrorNote message={error} />
        <div className="flex flex-wrap gap-2">
          {symbols.map((s) => (
            <Button key={s} onClick={() => explain(s)} disabled={busy}>
              Explain {s}
            </Button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="flex flex-1 flex-col gap-1 text-xs">
            <span className="text-slate-400">Question</span>
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ask(question)}
              placeholder="Ask about the current state…"
              className="rounded border border-slate-700 bg-ink/60 px-2 py-1.5"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-slate-400">Symbol (optional)</span>
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="rounded border border-slate-700 bg-ink/60 px-2 py-1.5"
            >
              <option value="">All</option>
              {symbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <Button tone="primary" onClick={() => ask(question)} disabled={busy || !question.trim()}>
            {busy ? "Thinking…" : "Ask"}
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => ask(s)}
              disabled={busy}
              className="rounded border border-slate-700/60 px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200"
            >
              {s}
            </button>
          ))}
        </div>
      </Card>

      <Card title="Analysis history">
        {history.data?.length ? (
          <ul className="space-y-3">
            {history.data.map((a) => (
              <li key={a.id} className="rounded border border-slate-700/50 bg-ink/40 p-3">
                <div className="mb-1 flex items-center justify-between text-[11px] text-slate-500">
                  <span>
                    {a.symbol || "portfolio"} · {a.model}
                  </span>
                  <span>{when(a.created_at)}</span>
                </div>
                <p className="mb-1 text-xs text-slate-400">{a.question}</p>
                <p className="whitespace-pre-wrap text-xs text-slate-200">{a.summary}</p>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No analyses yet. Ask a question above.</Empty>
        )}
      </Card>
    </div>
  );
}
