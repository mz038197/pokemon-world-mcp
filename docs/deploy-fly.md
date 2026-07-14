# Fly.io 部署（pokemon-world-mcp）

## 事前

- `flyctl auth login`
- **兩個** Neon／Postgres：
  - `DATABASE_URL`：與 `vans-mcp-server` / `vans-coding-router` **同一** router DB（只讀／更新 `api_keys`）
  - `POKEMON_DATABASE_URL`：**本專案專用**遊戲 DB（`pokemon_saves`、`pokemon_catalog_cache`）
- 自訂網域（選用）：Squarespace DNS `poke.vanscoding.com` → Fly；`fly certs add poke.vanscoding.com`
- 無 Fly volume；遊戲狀態在 `POKEMON_DATABASE_URL`。本機 SQLite ≠ production。

## Secrets

```powershell
Copy-Item config\fly.secrets.env.example "$HOME\.pokemon-world-mcp\fly.secrets.env"
notepad "$HOME\.pokemon-world-mcp\fly.secrets.env"
```

填入：

| Secret | 說明 |
|--------|------|
| `DATABASE_URL` | Router Neon（`api_keys`；與 vans 相同） |
| `POKEMON_DATABASE_URL` | 遊戲 Neon（存檔＋圖鑑快取；**必須與 router 不同**） |

`PUBLIC_URL` 放在 `fly.toml` 的 `[env]`，不要設成 Fly secret。

**不要**在 production 設定 `MCP_DEV_BYPASS_KEY`。

套用 secrets：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy-fly.ps1 -SecretsOnly
```

## 本機 SQLite（開發）

未設 `POKEMON_DATABASE_URL` 時，存檔與圖鑑快取寫入：

`%USERPROFILE%\.pokemon-world-mcp\pokemon_world.db`

可用 `SQLITE_PATH` 覆寫。**不要**把 `*.db` commit 進 repo。Production 請用 `POKEMON_DATABASE_URL`，不要依賴 Fly 容器內 SQLite。

本機驗證仍可用 `DATABASE_URL`（router）或 `MCP_DEV_BYPASS_KEY`。

## 圖鑑快取

- 快取在遊戲 DB 的 `pokemon_catalog_cache`
- **24 小時內**新鮮快取：不打 PokéAPI（`CATALOG_CACHE_TTL_HOURS`，預設 24）
- 缺快取或過期：打 PokéAPI 並寫回遊戲 DB
- 同一進程超過 TTL：讀取圖鑑時 `ensure_fresh` 會重載
- PokéAPI 失敗：記憶體 fallback；**不**把 fallback 寫進 DB

## 切換後清理 router Postgres

新遊戲 DB 部署並驗證 health／存檔／圖鑑後，從 **router**（`DATABASE_URL`）刪除本專案舊表（**不動** `api_keys`）：

```powershell
$env:DATABASE_URL = "postgresql://...router..."
$env:POKEMON_DATABASE_URL = "postgresql://...game..."
uv run python scripts\drop_router_pokemon_tables.py --yes
```

腳本會拒絕在兩個 URL 相同時執行。

## 首次開通

```powershell
fly apps create pokemon-world-mcp
# region 預設 sin（見 fly.toml）
powershell -ExecutionPolicy Bypass -File scripts\deploy-fly.ps1
```

`--ha=false` 維持單機器。

設定 CI（必備）：見下方 **CI（GitHub Actions）** — 建立 `FLY_API_TOKEN` 後 push `master` 即自動 deploy。

綁自訂網域後：

1. `fly certs add poke.vanscoding.com`
2. DNS：子網域 CNAME → `pokemon-world-mcp.fly.dev`
3. `fly.toml` `[env] PUBLIC_URL` 已設為 `https://poke.vanscoding.com`
4. 再 deploy 一次（改 DNS／憑證後）

## CI（GitHub Actions）

Workflow：`.github/workflows/fly-deploy.yml` — push `master` 時 `flyctl deploy --remote-only --ha=false`。

**一次性設定**（repo secrets，CI 不覆寫 Fly secrets）：

```powershell
fly tokens create deploy -x 999999h
# GitHub repo → Settings → Secrets → Actions → FLY_API_TOKEN
```

Secrets 仍用本機 `$HOME\.pokemon-world-mcp\fly.secrets.env` + `scripts\deploy-fly.ps1 -SecretsOnly` 套用；CI 只部署程式碼。

## 驗證

```powershell
curl https://pokemon-world-mcp.fly.dev/health
# 綁網域後：
curl https://poke.vanscoding.com/health
```

Health 應含 `"ok": true`、`auth`（有 router Neon 時為 `neon`）、`saves`（production 應為 `postgres`）。

## 與 vans 的關係

- App 分開：本服務只做 Pokémon World MCP；Notion／Calendar／Gmail 仍在 `vans-mcp-server`
- 共用 router Neon 的 `api_keys`：學生同一把 `vcr_sk_` 可驗證
- 遊戲表在獨立 Neon；啟動時對 `POKEMON_DATABASE_URL` 執行 `CREATE TABLE IF NOT EXISTS`
