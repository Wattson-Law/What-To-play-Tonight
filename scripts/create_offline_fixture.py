"""Create a tiny valid SQLite snapshot for local verification."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from coop_night_picker.constants import REVIEW_TIERS
from coop_night_picker.db import init_db, insert_quote, upsert_game


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="tmp-fixtures/valid.sqlite")
    args = parser.parse_args(argv)
    conn = init_db(args.output)
    upsert_game(conn, {"appid": 9001, "name": "Offline Fixture", "review_score": 9,
                       "review_tier_zh": REVIEW_TIERS[9]["zh"], "online_coop": 1,
                       "is_free": 1, "max_players": 4})
    insert_quote(conn, {"appid": 9001, "voted_up": 1, "quote_text": "离线验收引用",
                        "review_url": "https://steamcommunity.com/profiles/9001/recommended/9001/"})
    conn.close()
    print(f"created {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
