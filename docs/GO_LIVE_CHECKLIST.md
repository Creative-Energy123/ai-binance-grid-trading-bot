# Go-live checklist

Work through this in order. Every item is something you verify yourself — none of
it is asserted by the software on your behalf.

Nothing here makes live trading safe. It makes the failure modes *known*.

## 1. Backtesting works

- [ ] A backtest completes on at least 3,000 1m candles for each symbol you intend to trade.
- [ ] The train / validation / out-of-sample split is used (`split: true`, the default).
- [ ] Out-of-sample performance does not collapse relative to training — the API
      returns an explicit `overfitting_warning` when it does.
- [ ] The fee and slippage assumptions shown in the metrics match your real VIP
      tier and observed slippage, not the defaults.
- [ ] You have looked at the number of trades. A handful of trades is noise, not evidence.

## 2. Paper trading works

- [ ] The bot has run in `paper` mode long enough to see both a winning and a
      losing sequence — days, not hours.
- [ ] Trades appear in the journal with sensible fees, slippage and R multiples.
- [ ] You have seen the bot decline to trade and understood why, via the scanner's
      "Why?" trace or the AI assistant.

## 3. Testnet works

- [ ] Binance **testnet** keys are configured and validated.
- [ ] A real order was placed, filled, and its fill confirmed by reading the order back.
- [ ] A protective stop order appeared on the exchange after entry.
- [ ] A position closed through TP1 and the stop moved to breakeven.

## 4. Stop-loss works

- [ ] A stop-loss exit is visible in the journal with `exit_reason = stop_loss`.
- [ ] On testnet, the stop order existed on the exchange, not only in the database.
- [ ] You have verified that if stop placement fails, the position is closed
      immediately (`stop_order_failed` alert).

## 5. Daily loss limit works

- [ ] With a temporarily small `MAX_DAILY_LOSS_PCT`, the bot paused itself after
      breaching it and wrote a `daily_loss_limit` risk event.
- [ ] The limit was restored afterwards.

## 6. Emergency stop works

- [ ] The red Emergency stop button cancelled resting orders, closed positions,
      and recorded the event.
- [ ] The bot did not resume on its own.

## 7. API failure handling works

- [ ] With the network interrupted, the health strip turned red and the bot stopped
      taking new entries instead of trading on stale data.
- [ ] An `api_failure` alert reached your configured channel.

## 8. Position reconciliation works

- [ ] A manually opened position on the exchange was detected as a mismatch
      (`POSITION SYNC FAIL`) and paused the bot.

## 9. Fees and slippage are included

- [ ] Journal rows show non-zero `fees` and `slippage`.
- [ ] Backtest metrics report `costs_as_pct_of_gross`, and you are comfortable
      with how much of the gross edge the costs consume.

## 10. Credentials are correct

- [ ] The API key has **trading enabled and withdrawals disabled**.
- [ ] The key is IP-restricted to this server.
- [ ] `CREDENTIALS_ENCRYPTION_KEY` is set and backed up separately from the database.
- [ ] Live confirmation succeeded — it fails by design if the key can withdraw.

## 11. Operations

- [ ] Health and metrics are being scraped, with an alert on `scalper_health_ok == 0`.
- [ ] Alerts are reaching a channel you actually read.
- [ ] Database backups are running and one restore has been tested.
- [ ] Host time is synchronised.

## 12. Sizing

- [ ] `RISK_PER_TRADE_PCT` is 0.5% or lower.
- [ ] `FUTURES_LEVERAGE` is 3x or lower if futures are enabled.
- [ ] Starting capital is money you can afford to lose entirely.

---

Backtested and simulated results are not evidence of future returns. Completing
this checklist does not make the strategy profitable — it means that when it
loses, it loses in the ways you have already seen and bounded.
