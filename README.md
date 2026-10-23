# Xiaozhi serverless RSS news snapshot

**No Render, no VPS, no server daemon.** GitHub Actions fetches RSS every 3 hours and publishes `data/news.json` via `raw.githubusercontent.com`. Python stdlib only. Public repositories qualify for GitHub-hosted standard runner free usage subject to GitHub's changing policies and fair-use/limits. Scheduled workflows can be delayed or disabled; upstream feeds can return HTTP 403. Zero ongoing operator work or 100% availability is not guaranteed.

## Install in a new public repository

1. Create a **public** GitHub repo, e.g. `xiaozhi-news-static` (or use a separate branch/repo; do not overwrite your original `xiaozhi-rss-news` production branch).
2. Upload `build_news.py`, `.github/workflows/update-news.yml`, and optionally this `README.md`; `data/news.json` is created by the action.
3. Repo → Settings → Actions → General → Workflow permissions → **Read and write permissions** (if needed to allow bot commits).
4. Actions → **Refresh Xiaozhi News JSON** → **Run workflow**. Wait for successful run and check `data/news.json`.
5. Static URL: `https://raw.githubusercontent.com/infotaxrana-ai/xiaozhi-news-static/main/data/news.json` if that is the repository name.

## Important limitations

- Produces **data**, not an MCP Tool, audio, or a live WebSocket. The current original `index.js` requires a continuously running MCP WebSocket to register `get_latest_news` and keep queue/next/continue/skip/stop state. GitHub Actions/static JSON cannot replace it on its own.
- ESP32 firmware or an existing already-running MCP agent must be explicitly adapted to read the static URL and maintain interaction state. This package does NOT change firmware, `MCP_Bridge`, JioSaavn or original `xiaozhi-rss-news`.
- Headlines/content returned by RSS sources may fail or be restricted. No fabricated news; stale data must be treated as stale by clients.
- GitHub Actions scheduled runs can be delayed. GitHub may disable inactive scheduled workflows and restrictions/pricing can change.
