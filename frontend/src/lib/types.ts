export type BotStatus = {
  mode: string;
  running: boolean;
  paused: boolean;
  emergency_stopped: boolean;
  live_confirmed: boolean;
  status_message: string;
  equity: number;
  peak_equity: number;
  realized_pnl: number;
  consecutive_losses: number;
  cooldown_until: string | null;
  last_tick_at: string | null;
  symbols: string[];
  futures_enabled: boolean;
  leverage: number;
  places_real_orders: boolean;
};

export type Overview = {
  equity: number;
  balance: number;
  available: number;
  today_pnl: number;
  week_pnl: number;
  month_pnl: number;
  win_rate: number;
  profit_factor: number;
  max_drawdown_pct: number;
  trades: number;
  open_positions: number;
  unrealized_pnl: number;
};

export type Position = {
  id: number;
  symbol: string;
  side: string;
  strategy: string;
  mode: string;
  entry_price: number;
  quantity: number;
  remaining_quantity: number;
  stop_price: number;
  tp1_price: number;
  tp2_price: number;
  tp1_filled: boolean;
  leverage: number;
  liquidation_price: number | null;
  unrealized_pnl: number;
  realized_pnl: number;
  risk_amount: number;
  signal_score: number;
  entry_reason: string;
  opened_at: string | null;
};

export type ScanRow = {
  symbol: string;
  price?: number;
  regime?: string;
  regime_confidence?: number;
  tradable?: boolean;
  signal?: string | null;
  strategy?: string | null;
  score?: number;
  rejected_reason?: string | null;
  rsi?: number | null;
  adx?: number | null;
  atr_pct?: number | null;
  volume_ratio?: number | null;
  trend?: string;
  spread_pct?: number | null;
  liquid?: boolean;
  strategies_enabled?: Record<string, boolean>;
  error?: string;
};

export type Trade = {
  id: number;
  symbol: string;
  strategy: string;
  side: string;
  mode: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  fees: number;
  slippage: number;
  pnl: number;
  pnl_pct: number;
  r_multiple: number;
  duration_seconds: number;
  regime: string;
  signal_score: number;
  entry_reason: string;
  exit_reason: string;
  closed_at: string | null;
};

export type SignalRow = {
  id: number;
  symbol: string;
  side: string;
  strategy: string;
  regime: string;
  score: number;
  score_breakdown: Record<string, any>;
  entry_price: number;
  stop_price: number;
  tp1_price: number;
  tp2_price: number;
  reward_risk: number;
  accepted: boolean;
  rejection_reason: string;
  reasons: string[];
  created_at: string;
};

export type HealthComponent = { name: string; status: string; detail: string };

export type Health = {
  status: string;
  mode: string;
  ok: boolean;
  components: HealthComponent[];
  ai_configured: boolean;
};

export type BacktestSegment = {
  segment: string;
  candles: number;
  starting_equity: number;
  ending_equity: number;
  metrics: Record<string, any>;
  equity_curve: { t: number; equity: number }[];
  drawdown_curve: { t: number; dd_pct: number }[];
  warnings: string[];
};

export type BacktestResponse = {
  symbol: string;
  segments: BacktestSegment[];
  overfitting_warning: string | null;
  disclaimer: string;
};

export type RiskEvent = {
  id: number;
  event_type: string;
  severity: string;
  symbol: string;
  detail: string;
  created_at: string;
};

export type AiAnalysis = {
  id: number;
  symbol: string;
  question: string;
  summary: string;
  model: string;
  created_at: string;
};
