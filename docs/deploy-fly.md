# Fly.io 部署（pokemon-world-mcp）

## 事前

- `flyctl auth login`
- Neon：與 `vans-mcp-server` / `vans-coding-router` **同一** `DATABASE_URL`（讀 `api_keys`，寫 `pokemon_saves`）
- 自訂網域（選用）：Squarespace DNS `poke.vanscoding.com` → Fly；`fly certs add poke.vanscoding.com`
- 無 Fly volume；狀態在 Neon。本機 SQLite ≠ production。

## Secrets

```powershell
Copy-Item config\fly.secrets.env.example "$HOME\.pokemon-world-mcp\fly.secrets.env"
notepad "$HOME\.pokemon-world-mcp\fly.secrets.env"
```

填入：

| Secret | 說明 |
|--------|------|
| `DATABASE_URL` | 與 vans **同一** Neon connection string |

`PUBLIC_URL` 放在 `fly.toml` 的 `[env]`，不要設成 Fly secret。

**不要**在 production 設定 `MCP_DEV_BYPASS_KEY`。

套用 secrets：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy-fly.ps1 -SecretsOnly
```

## 本機 SQLite（開發）

未設 `DATABASE_URL` 時，存檔寫入：

`%USERPROFILE%\.pokemon-world-mcp\pokemon_world.db`

可用 `SQLITE_PATH` 覆寫。**不要**把 `*.db` commit 進 repo。Production 請用 Neon，不要依賴 Fly 容器內 SQLite。

## 首次開通

```powershell
fly apps create pokemon-world-mcp
# region 預設 sin（見 fly.toml）
powershell -ExecutionPolicy Bypass -File scripts\deploy-fly.ps1
```

`--ha=false` 維持單機器。

綁自訂網域後：

1. `fly certs add poke.vanscoding.com`
2. DNS：子網域 CNAME → `pokemon-world-mcp.fly.dev`
3. 把 `fly.toml` `[env] PUBLIC_URL` 改成 `https://poke.vanscoding.com`
4. 再 deploy 一次

## CI（選用，本次未加 workflow）

若之後要加：push `main` → `flyctl deploy --remote-only --ha=false`；deploy token 放 GitHub `FLY_API_TOKEN`；CI 不覆寫 secrets。

## 驗證

```powershell
curl https://pokemon-world-mcp.fly.dev/health
# 綁網域後：
curl https://poke.vanscoding.com/health
```

Health 應含 `"ok": true`、`auth`（有 Neon 時為 `neon`）、`saves`（production 應為 `postgres`）。

## 與 vans 的關係

- App 分開：本服務只做 Pokémon World MCP；Notion／Calendar／Gmail 仍在 `vans-mcp-server`
- 共用 Neon：學生同一把 `vcr_sk_` 可驗證
- 本服務啟動時 `CREATE TABLE IF NOT EXISTS pokemon_saves`
