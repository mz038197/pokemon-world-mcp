# Fly.io 部署（pokemon-world-mcp）

## 事前

- `flyctl auth login`
- 金鑰驗證走 router 的 Key Check（`POST https://ai.vanscoding.com/internal/key-check`），**不連** router 的資料庫
- 本服務的 Service Credential：`openssl rand -hex 32` 產生，SHA-256 加進 router 的 `VCR_SERVICE_TOKENS`（router 的 guide/DEPLOYMENT.md「Service Credential（Key Check）」）
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
| `VCR_SERVICE_CREDENTIAL` | 本服務呼叫 Key Check 用的 Service Credential（必填；沒設定時照樣啟動，但 router 回 401，所有金鑰都當作 unavailable，每次送一筆 ERROR Signal） |
| `POKEMON_DATABASE_URL` | 遊戲 Neon（存檔＋圖鑑快取） |

`VCR_URL` 預設 `https://ai.vanscoding.com`，通常不用設定。

`PUBLIC_URL` 放在 `fly.toml` 的 `[env]`，不要設成 Fly secret。

**不要**在 production 設定 `MCP_DEV_BYPASS_KEY`：有 `FLY_APP_NAME` 又設定它時，服務拒絕啟動。

套用 secrets：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy-fly.ps1 -SecretsOnly
```

## 本機 SQLite（開發）

未設 `POKEMON_DATABASE_URL` 時，存檔與圖鑑快取寫入：

`%USERPROFILE%\.pokemon-world-mcp\pokemon_world.db`

可用 `SQLITE_PATH` 覆寫。**不要**把 `*.db` commit 進 repo。Production 請用 `POKEMON_DATABASE_URL`，不要依賴 Fly 容器內 SQLite。

本機驗證用 `MCP_DEV_BYPASS_KEY`，或設定 `VCR_SERVICE_CREDENTIAL` 走 Key Check。

## 圖鑑快取

- 快取在遊戲 DB 的 `pokemon_catalog_cache`
- **24 小時內**新鮮快取：不打 PokéAPI（`CATALOG_CACHE_TTL_HOURS`，預設 24）
- 缺快取或過期：打 PokéAPI 並寫回遊戲 DB
- 同一進程超過 TTL：讀取圖鑑時 `ensure_fresh` 會重載
- PokéAPI 失敗：記憶體 fallback；**不**把 fallback 寫進 DB

## 改用 Key Check（移除 router 的 `DATABASE_URL`）

1. router 的 `VCR_SERVICE_TOKENS` 已有本服務 credential 的雜湊並已部署
2. `fly secrets set VCR_SERVICE_CREDENTIAL=...`（或寫進 `fly.secrets.env` 後 `deploy-fly.ps1 -SecretsOnly`）
3. 部署本版
4. `fly secrets unset DATABASE_URL`（`deploy-fly.ps1` 只會 set，不會 unset）
5. 驗證：`/health` 的 `auth` 為 `key_check`；用學生金鑰呼叫工具，存檔仍在

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

Health 應含 `"ok": true`、`auth`（`key_check`）、`saves`（production 應為 `postgres`）。

## 與 vans 的關係

- App 分開：本服務只做 Pokémon World MCP；Notion／Calendar／Gmail 仍在 `vans-mcp-server`
- 學生同一把 `vcr_sk_` 透過 router 的 Key Check 驗證；存檔用 router 的整數 `user_id`（Classroom 的 `student_id`、Personal 的 `user_id`）
- 遊戲表在獨立 Neon；啟動時對 `POKEMON_DATABASE_URL` 執行 `CREATE TABLE IF NOT EXISTS`
