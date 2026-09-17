import json
import os
import time
from pathlib import Path

import feedparser
import httpx
from atproto import Client, models
from bs4 import BeautifulSoup

RSS_URLS = [
    "https://zenn.dev/topics/claudecode/feed",
    "https://zenn.dev/topics/codex/feed",
    "https://zenn.dev/topics/claude/feed",
    "https://zenn.dev/topics/chatgpt/feed",
    "https://zenn.dev/topics/openai",
    "https://zenn.dev/topics/qwen/feed",
    "https://zenn.dev/topics/anthropic/feed",
    "https://zenn.dev/topics/gemini/feed",
    "https://zenn.dev/topics/llamacpp/feed",
    "https://zenn.dev/topics/deepseek/feed",
]
STATE_FILE = Path("data/posted_ids.json")
BLUESKY_MAX_GRAPHEMES = 300


def load_state() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    with STATE_FILE.open() as f:
        data = json.load(f)
    return set(data.get("posted_ids", []))


def save_state(posted_ids: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w") as f:
        json.dump({"posted_ids": sorted(posted_ids)}, f, ensure_ascii=False, indent=2)


def fetch_new_entries(posted_ids: set[str]) -> list[dict]:
    new_entries = []

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)
        ns = [e for e in feed.entries if e.id not in posted_ids]

        for e in ns:
            posted_ids.add(e.id)

        new_entries += ns

    # 古い順にソート
    new_entries.sort(key=lambda e: e.get("published_parsed") or 0)

    return new_entries


def build_post_text(title: str, author: str, url: str) -> str:
    # タイトル | 著者 #zenn の後に改行してURLを配置
    suffix = f" | {author} #zenn\n{url}"
    max_title_graphemes = BLUESKY_MAX_GRAPHEMES - len(list(suffix))
    graphemes = list(title)
    if len(graphemes) > max_title_graphemes:
        title = "".join(graphemes[: max_title_graphemes - 3]) + "..."
    return title + suffix


def fetch_ogp(url: str) -> dict:
    """記事URLからOGPメタデータを取得する。失敗した場合は空dictを返す。"""
    try:
        resp = httpx.get(
            url,
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"OGP fetch failed for {url}: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    def og(prop: str) -> str:
        tag = soup.find("meta", property=f"og:{prop}") or soup.find(
            "meta", attrs={"name": f"og:{prop}"}
        )
        return tag["content"] if tag and tag.get("content") else ""

    return {
        "title": og("title") or soup.title.string if soup.title else "",
        "description": og("description"),
        "image_url": og("image"),
    }


def upload_image(client: Client, image_url: str):
    """OG画像をダウンロードしてBlueSkyにアップロードする。失敗した場合はNoneを返す。"""
    if not image_url:
        return None
    try:
        resp = httpx.get(image_url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        blob = client.upload_blob(resp.content)
        return blob.blob
    except Exception as e:
        print(f"Image upload failed: {e}")
        return None


def build_embed(client: Client, url: str) -> models.AppBskyEmbedExternal.Main | None:
    """リンクカード用のembedを構築する。OGP取得に失敗した場合はNoneを返す。"""
    ogp = fetch_ogp(url)
    if not ogp:
        return None

    thumb = upload_image(client, ogp.get("image_url", ""))

    return models.AppBskyEmbedExternal.Main(
        external=models.AppBskyEmbedExternal.External(
            uri=url,
            title=ogp.get("title", ""),
            description=ogp.get("description", ""),
            thumb=thumb,
        )
    )


def build_facets(text: str, url: str) -> list:
    """テキスト内の #zenn と 記事URL をFacet（タグ・リンク）として返す。"""
    facets = []
    text_bytes = text.encode("utf-8")

    # 1. #zenn ハッシュタグの指定
    tag_bytes = b"#zenn"
    idx = text_bytes.find(tag_bytes)
    if idx != -1:
        facets.append(
            models.AppBskyRichtextFacet.Main(
                features=[models.AppBskyRichtextFacet.Tag(tag="zenn")],
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=idx,
                    byte_end=idx + len(tag_bytes),
                ),
            )
        )

    # 2. 記事URL のリンク指定
    if url:
        url_bytes = url.encode("utf-8")
        url_idx = text_bytes.find(url_bytes)
        if url_idx != -1:
            facets.append(
                models.AppBskyRichtextFacet.Main(
                    features=[models.AppBskyRichtextFacet.Link(uri=url)],
                    index=models.AppBskyRichtextFacet.ByteSlice(
                        byte_start=url_idx,
                        byte_end=url_idx + len(url_bytes),
                    ),
                )
            )

    return facets


def post_to_bluesky(client: Client, entry: dict) -> None:
    title = entry.get("title", "(no title)")
    author = entry.get("author", "")
    url = entry.get("link", "")

    text = build_post_text(title, author, url)  # url を追加
    embed = build_embed(client, url)
    facets = build_facets(text, url)  # url を追加

    client.send_post(
        text=text,
        embed=embed,
        facets=facets,
        langs=["ja"],
    )
    print(f"Posted: {title}")


def main() -> None:
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    posted_ids = load_state()
    new_entries = fetch_new_entries(posted_ids)

    if not new_entries:
        print("No new articles.")
        return

    print(f"Found {len(new_entries)} new article(s).")

    if dry_run:
        print("\n--- DRY RUN: 以下の内容が投稿されます ---")
        for entry in new_entries:
            title = entry.get("title", "(no title)")
            author = entry.get("author", "")
            url = entry.get("link", "")
            text = build_post_text(title, author, url)  # url を追加
            facets = build_facets(text, url)  # url を追加
            print(f"\n{text}")
            if facets:
                for f in facets:
                    feature = f.features[0]
                    start = f.index.byte_start
                    end = f.index.byte_end
                    if isinstance(feature, models.AppBskyRichtextFacet.Tag):
                        print(f"  facet: #{feature.tag} (bytes {start}-{end})")
                    elif isinstance(feature, models.AppBskyRichtextFacet.Link):
                        print(f"  facet: Link({feature.uri}) (bytes {start}-{end})")
            print("-" * 40)
        return

    identifier = os.environ["BLUESKY_IDENTIFIER"]
    app_password = os.environ["BLUESKY_APP_PASSWORD"]
    client = Client()
    client.login(identifier, app_password)

    for entry in new_entries:
        try:
            post_to_bluesky(client, entry)
            posted_ids.add(entry.id)
            save_state(posted_ids)
        except Exception as e:
            print(f"Failed to post '{entry.get('title')}': {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()
