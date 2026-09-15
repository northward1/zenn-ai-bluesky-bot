# zenn-rust-bluesky-bot

[Zenn の Rust トピック](https://zenn.dev/topics/rust) の新着記事を Bluesky に自動投稿するBotです。
GitHub Actions で1時間ごとに実行されます。

## セットアップ

### 1. Bluesky アプリパスワードを取得

[bsky.app](https://bsky.app) にログインし、
**Settings > Privacy and Security > App Passwords** でアプリパスワードを生成します。

### 2. GitHub Secrets を設定

リポジトリの **Settings > Secrets and variables > Actions** に以下を追加します。

| Secret 名 | 値 |
|---|---|
| `BLUESKY_IDENTIFIER` | Bluesky ハンドル（例: `yourname.bsky.social`） |
| `BLUESKY_APP_PASSWORD` | 手順1で生成したアプリパスワード |

### 3. GitHub Actions を有効化

リポジトリの **Actions** タブを開き、ワークフローを有効化します。

以降、毎時0分に自動実行されます。**Actions > Zenn Rust RSS Bot > Run workflow** から手動実行も可能です。

## ローカル実行

```bash
cp .env.example .env
# .env に認証情報を記入
export $(cat .env | xargs)
uv run bot.py
```

## 仕組み

1. `https://zenn.dev/topics/rust/feed` から RSS を取得
2. `data/posted_ids.json` と照合して未投稿の記事を抽出
3. Bluesky に投稿（URLはクリッカブルリンク付き）
4. `data/posted_ids.json` を更新してリポジトリにコミット
