# Stock Tracker Bot

[Русская версия](README.md) · ![tests](https://github.com/kot2015311/stock-tracker-bot/actions/workflows/tests.yml/badge.svg)

A Telegram bot for a private investor on the Moscow Exchange. It tracks prices and a portfolio (short positions included),
collects news from broker Telegram channels and RSS feeds on its own, and sends a scheduled report file:
portfolio, market, prices with technical indicators, dividends, news about your stocks and analyst reviews.

## Features

- **Scheduled reports** (10:05, 16:05 and 19:05 on weekdays by default) and on demand:
  - portfolio: result per position, realized and unrealized P&L, short-position risk;
  - market: MOEX index, government bond index, Brent futures, central bank FX rates and key rate;
  - prices: 1/5/20-day change, range, RSI(14), position vs. SMA50 and SMA200, volume — explained in plain words;
  - dividends: announced payouts with the "buy before" date and the amount for your shares;
  - news about your stocks for the last 3 days, full broker reviews, macro news, your own materials.
- **News without copy-paste:** reads public Telegram channels through the `t.me/s/…` web preview and RSS feeds.
  No Telegram account login needed.
- **Your materials:** forward a post, send a link, text or a `.txt`/`.html` file — the bot extracts the article
  and strips menus, comments, footers and duplicates.
- **Portfolio by commands:** `/buy`, `/sell` (selling more than you hold opens a short), `/pos`.
  Average price and realized P&L are calculated automatically.
- **Alerts:** price moves by N %, price levels (`/alert NVTK 1170`) and exit levels from your plan (`/setplan`).
- **Multi-user:** the owner grants access; each user has their own watchlist, portfolio and reports.
- **Source check** `/check`: what is reachable from your network and how to fix what is blocked.

The bot shows data and facts; it is not investment advice and does not trade.

## How it works

```
 Telegram channels (t.me/s) ─┐
 RSS feeds ──────────────────┼─► news collector (every 10 min) ─► data/news_store.json ─┐
 forwarded posts ────────────┘                                                          │
                                                                                        ▼
 T-Invest API (prices, dividends) ─┐                                           report builder ─► file in Telegram
 MOEX ISS (candles, Brent) ────────┼─► indicators ─────────────────────────────►     ▲
 Bank of Russia (FX, key rate) ────┘                                                  │
 data/portfolio.json (/buy, /sell, /pos) ─────────────────────────────────────────────┘
```

## Tech stack

Python 3.10+ · aiogram 3 · aiohttp · T-Invest gRPC API · MOEX ISS · BeautifulSoup · trafilatura · unittest + GitHub Actions

## Quick start

```bash
git clone https://github.com/kot2015311/stock-tracker-bot.git
cd stock-tracker-bot
python -m venv venv && venv\Scripts\activate   # Windows; on macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
cp .env.example .env    # BOT_TOKEN, TINKOFF_TOKEN (read-only), OWNER_ID
python check_sources.py --apply
python main.py
```

## Tests

99 tests run offline with no tokens: network and exchange calls are replaced with fakes.

```bash
python -m unittest discover -s tests -v
```

## Project structure

| Module | Responsibility |
|---|---|
| `main.py` | bot start-up and background tasks |
| `handlers.py` | Telegram buttons and commands |
| `scheduler.py` | report schedule, news collection, price and level checks |
| `report.py` | report builder |
| `news_store.py`, `telegram_channels.py`, `news.py`, `articles.py` | news collection, parsing and cleanup |
| `market_data.py`, `tinkoff_client.py` | MOEX ISS, Bank of Russia and T-Invest API clients |
| `indicators.py` | SMA, RSI, ATR, ranges and plain-language descriptions |
| `portfolio.py`, `alerts.py` | positions, trades, long/short P&L, alerts |

## Security

Tokens, certificates and the `data/` folder are git-ignored. The broker token should be read-only.

## License

MIT, see [LICENSE](LICENSE).
