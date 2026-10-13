import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://follow.yahoo.co.jp/themes/070fcbe035611a0a3376/"
OUTPUT_FILE = "yahoo_fukuyama.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}


def fetch_page():
    for attempt in range(1, 4):
        try:
            print(f"取得 {attempt}/3")

            response = requests.get(
                SOURCE_URL,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")
            response.raise_for_status()

            return response.text

        except requests.RequestException as e:
            print(f"取得失敗: {e}")

            if attempt < 3:
                print("10秒待って再試行します。")
                time.sleep(10)

    return None


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall("./channel/item"):
            guid = item.findtext("guid", "").strip()

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext("title", ""),
                "link": item.findtext("link", ""),
                "description": item.findtext(
                    "description", ""
                ),
                "pubDate": item.findtext("pubDate", ""),
                "guid": guid,
            }

    except Exception as e:
        print(f"既存RSS読込失敗: {e}")

    return items


def relative_time_to_datetime(relative_text, now):
    """
    Yahoo!の
    27分前
    1時間前
    2日前
    などを概算日時へ変換する。
    """

    relative_text = relative_text.strip()

    match = re.fullmatch(
        r"(\d+)分前",
        relative_text
    )

    if match:
        return now - timedelta(
            minutes=int(match.group(1))
        )

    match = re.fullmatch(
        r"(\d+)時間前",
        relative_text
    )

    if match:
        return now - timedelta(
            hours=int(match.group(1))
        )

    match = re.fullmatch(
        r"(\d+)日前",
        relative_text
    )

    if match:
        return now - timedelta(
            days=int(match.group(1))
        )

    match = re.fullmatch(
        r"(\d+)週間前",
        relative_text
    )

    if match:
        return now - timedelta(
            weeks=int(match.group(1))
        )

    return None


def split_display_text(text):
    """
    リンク文字列末尾の

    「 - 27分前」
    「 - 1時間前」
    「 - 2日前」

    などを分離する。

    戻り値:
    本文部分, 経過時間
    """

    pattern = (
        r"\s*-\s*"
        r"("
        r"\d+分前|"
        r"\d+時間前|"
        r"\d+日前|"
        r"\d+週間前"
        r")$"
    )

    match = re.search(pattern, text)

    if not match:
        return text.strip(), ""

    relative_text = match.group(1)

    main_text = text[
        :match.start()
    ].strip()

    return main_text, relative_text


def parse_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    articles = []
    seen_links = set()

    now = datetime.now(JST)

    for a in soup.find_all("a", href=True):
        href = a.get(
            "href", ""
        ).strip()

        if not (
            href.startswith(
                "https://news.yahoo.co.jp/"
            )
            or href.startswith(
                "https://article.yahoo.co.jp/"
            )
        ):
            continue

        link = urljoin(
            SOURCE_URL,
            href
        )

        if link in seen_links:
            continue

        raw_text = clean_text(
            a.get_text(
                " ",
                strip=True
            )
        )

        if not raw_text:
            continue

        if raw_text == "報告":
            continue

        # 動画の「0:37」などが
        # 先頭に入る場合だけ除去
        raw_text = re.sub(
            r"^\d+:\d+\s*",
            "",
            raw_text
        ).strip()

        if len(raw_text) < 8:
            continue

        display_text, relative_text = (
            split_display_text(raw_text)
        )

        if not display_text:
            continue

        pub_dt = None

        if relative_text:
            pub_dt = relative_time_to_datetime(
                relative_text,
                now
            )

        seen_links.add(link)

        articles.append(
            {
                "title": display_text,
                "link": link,
                "relative": relative_text,
                "pub_dt": pub_dt,
            }
        )

    return articles


def build_rss(
    existing_items,
    current_articles
):
    now = datetime.now(JST)

    merged = dict(existing_items)
    new_count = 0

    for index, article in enumerate(
        current_articles
    ):
        guid = make_guid(
            article["link"]
        )

        if guid in merged:
            continue

        pub_dt = article["pub_dt"]

        # Yahoo!側から相対時刻を
        # 読み取れなかった場合だけ
        # 現在時刻を使用
        if pub_dt is None:
            pub_dt = (
                now
                - timedelta(seconds=index)
            )

        description_parts = []

        if article["relative"]:
            description_parts.append(
                f"Yahoo!表示: {article['relative']}"
            )

        description_parts.append(
            article["title"]
        )

        description = " / ".join(
            description_parts
        )

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": description,
            "pubDate": format_datetime(
                pub_dt
            ),
            "guid": guid,
        }

        new_count += 1

    def get_pubdate(item):
        try:
            return parsedate_to_datetime(
                item["pubDate"]
            )
        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0"
    )

    channel = ET.SubElement(
        rss,
        "channel"
    )

    ET.SubElement(
        channel,
        "title"
    ).text = "Yahoo! 福山市"

    ET.SubElement(
        channel,
        "link"
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description"
    ).text = (
        "Yahoo! JAPAN「フォロー」の"
        "福山市テーマに掲載された記事"
    )

    ET.SubElement(
        channel,
        "language"
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate"
    ).text = format_datetime(now)

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item"
        )

        ET.SubElement(
            item,
            "title"
        ).text = data["title"]

        ET.SubElement(
            item,
            "link"
        ).text = data["link"]

        ET.SubElement(
            item,
            "description"
        ).text = data["description"]

        ET.SubElement(
            item,
            "pubDate"
        ).text = data["pubDate"]

        guid_el = ET.SubElement(
            item,
            "guid"
        )

        guid_el.set(
            "isPermaLink",
            "false"
        )

        guid_el.text = data["guid"]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  "
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page()

    if html is None:
        if existing_items:
            print(
                "サイトを取得できないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "Yahoo! 福山市を取得できず、"
            "既存XMLもありません。"
        )

    articles = parse_articles(html)

    print(
        f"現在ページの記事候補: "
        f"{len(articles)}"
    )

    if len(articles) < 5:
        if existing_items:
            print(
                "記事数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "記事取得数が少なすぎます: "
            f"{len(articles)}"
        )

    print()
    print("取得記事:")

    for i, article in enumerate(
        articles,
        1
    ):
        print(
            f"{i:2d}. "
            f"{article['title']}"
        )

        if article["relative"]:
            print(
                f"    Yahoo!表示: "
                f"{article['relative']}"
            )

        if article["pub_dt"]:
            print(
                "    概算日時: "
                + article[
                    "pub_dt"
                ].strftime(
                    "%Y-%m-%d "
                    "%H:%M:%S %z"
                )
            )

        print(
            f"    {article['link']}"
        )

    total_count, new_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"今回の新規記事: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        "保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()