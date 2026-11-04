REVIEW_TIERS = {
    9: {"en": "Overwhelmingly Positive", "zh": "好评如潮", "stars": "★★★★★", "score": 5.0},
    8: {"en": "Very Positive", "zh": "特别好评", "stars": "★★★★½", "score": 4.5},
    7: {"en": "Positive", "zh": "好评", "stars": "★★★★", "score": 4.0},
    6: {"en": "Mostly Positive", "zh": "多半好评", "stars": "★★★½", "score": 3.5},
    5: {"en": "Mixed", "zh": "褒贬不一", "stars": "★★★", "score": 3.0},
    4: {"en": "Mostly Negative", "zh": "多半差评", "stars": "★★½", "score": 2.5},
    3: {"en": "Negative", "zh": "差评", "stars": "★★", "score": 2.0},
    2: {"en": "Very Negative", "zh": "特别差评", "stars": "★½", "score": 1.5},
    1: {"en": "Overwhelmingly Negative", "zh": "差评如潮", "stars": "★", "score": 1.0},
    0: {"en": "No Enough Reviews", "zh": "评测不足/试玩区", "stars": "☆", "score": 0.0},
}

CATEGORY_ZH = {
    1: "多人",
    2: "单人",
    9: "合作",
    10: "游戏试玩(Demo)",
    20: "大型多人在线",
    24: "同屏/分屏",
    27: "跨平台多人",
    36: "在线PvP",
    37: "同屏/分屏PvP",
    38: "在线合作",
    39: "同屏/分屏合作",
    44: "远程同乐",
    47: "局域网PvP",
    48: "局域网合作",
    49: "玩家对战(PvP)",
}

TAG_ZH = {
    19: "动作",
    21: "冒险",
    113: "免费开玩",
    492: "独立",
    493: "抢先体验",
    597: "休闲",
    599: "模拟",
    1662: "生存",
    1664: "解谜",
    1667: "恐怖",
    1685: "合作",
    1687: "潜行",
    1695: "开放世界",
    1702: "制作",
    1716: "肉鸽",
    1719: "喜剧",
    1721: "心理恐怖",
    1774: "射击",
    3810: "沙盒",
    3841: "本地合作",
    3843: "在线合作",
    3859: "多人",
    3959: "轻度肉鸽",
    3968: "物理",
    3978: "生存恐怖",
    4136: "欢乐",
    4508: "合作战役",
    5711: "团队导向",
    7178: "派对游戏",
    7368: "本地多人",
    9157: "水底",
    19780: "潜水艇",
}

HORROR_TAG_IDS = {1667, 1721, 3978}
COOP_TAG_IDS = {1685, 3843, 3841, 4508, 7368}
QUIRKY_TAG_IDS = {4136, 1719, 3968, 7178, 5711}

NEGATIVE_CURATOR_PATTERNS = r"阉割|赶工|不耐玩|不推荐|快逃|别买|坐牢|半成品|差评|失望|敷衍|粗制滥造|骗钱"

# Automated zero-human-intervention discovery feeds for quirky/funny/physics co-op games & demos
FRIENDSLOP_FEEDS = [
    {"name": "Steam热门愿望单·欢乐联机新游", "category1": 998, "tags": "3843,4136", "filter": "popularwishlist"},
    {"name": "Steam热门愿望单·喜剧联机新游", "category1": 998, "tags": "3843,1719", "filter": "popularwishlist"},
    {"name": "Steam物理互坑·欢乐联机热榜", "category1": 998, "tags": "3843,3968,4136"},
    {"name": "Steam社交派对·欢乐联机热榜", "category1": 998, "tags": "3843,7178,4136"},
    {"name": "Steam惊悚整活·欢乐联机热榜", "category1": 998, "tags": "3843,1667,4136"},
    {"name": "Steam独立整活·最新发售榜", "category1": 998, "tags": "3843,4136,492", "sort_by": "Released_DESC"},
    {"name": "Steam欢乐联机·热门试玩Demo榜", "category1": 10, "category3": 38, "tags": "4136"},
    {"name": "Steam物理互坑·热门试玩Demo榜", "category1": 10, "category3": 38, "tags": "3968"},
]

CURATOR_SOURCES = [
    {"clanid": 6859276, "name": "Co-Optimus", "focus": "全球联机合作游戏垂直权威"},
    {"clanid": 29519836, "name": "UP鉴游组", "focus": "独立与联机好游发掘"},
    {"clanid": 34753839, "name": "好玩✔Fun", "focus": "高可玩性独立与联机"},
    {"clanid": 42532812, "name": "游饼", "focus": "中文新游与试玩评测"},
    {"clanid": 41542789, "name": "小黑盒-盒娘后援会", "focus": "中文热门/独立/新品试玩"},
    {"clanid": 27093039, "name": "中国鉴赏家同好会", "focus": "中文头部综合/独立游戏"},
    {"clanid": 10566883, "name": "中国玩家鉴赏组", "focus": "中文玩家深度鉴赏"},
    {"clanid": 10436844, "name": "CowLevel.Net", "focus": "奶牛关硬核与独立精选"},
]

TAG_ALIASES = {
    "indie": ["独立", "Indie", "492"],
    "独立": ["独立", "Indie", "492"],
    "独立游戏": ["独立", "Indie", "492"],
    "demo": ["游戏试玩(Demo)", "Demo", "试玩"],
    "试玩": ["游戏试玩(Demo)", "Demo", "试玩"],
    "survival": ["生存", "生存恐怖", "Survival", "1662", "3978"],
    "生存": ["生存", "生存恐怖", "Survival", "1662", "3978"],
    "puzzle": ["解谜", "Puzzle", "1664"],
    "解谜": ["解谜", "Puzzle", "1664"],
    "roguelike": ["肉鸽", "轻度肉鸽", "Roguelike", "Roguelite", "1716", "3959"],
    "肉鸽": ["肉鸽", "轻度肉鸽", "Roguelike", "Roguelite", "1716", "3959"],
    "sandbox": ["沙盒", "开放世界", "Sandbox", "3810", "1695"],
    "沙盒": ["沙盒", "开放世界", "Sandbox", "3810", "1695"],
    "funny": ["欢乐", "喜剧", "物理", "派对游戏", "休闲", "Funny", "4136", "1719", "3968", "7178"],
    "欢乐": ["欢乐", "喜剧", "物理", "派对游戏", "休闲", "Funny", "4136", "1719", "3968", "7178"],
    "搞笑": ["欢乐", "喜剧", "物理", "派对游戏", "4136", "1719", "3968", "7178"],
    "整活": ["欢乐", "喜剧", "物理", "派对游戏", "4136", "1719", "3968", "7178"],
    "派对": ["派对游戏", "欢乐", "喜剧", "7178", "4136", "1719"],
    "物理": ["物理", "欢乐", "3968", "4136"],
    "shooter": ["射击", "Shooter", "FPS", "1774"],
    "射击": ["射击", "Shooter", "FPS", "1774"],
}

REVIEW_KEYWORDS = {
    "coop_quality": r"联机|开黑|组队|队友|朋友|基友|双人|四人|多人|配合|欢乐|友尽|互坑|搞笑|节目效果",
    "network": r"延迟|卡顿|掉线|加速器|网络|ping|断连|服务器|连不上|房主",
    "session": r"一局|几小时|小时|通宵|一晚上|耐玩|流程|通关|快节奏",
    "horror": r"吓人|恐怖|高能|jump\s*scare|跳脸|诡异|害怕|胆小|微恐",
    "dealbreaker": r"退款|垃圾|坐牢|快逃|别买|外挂|挂壁|闪退|恶性bug|半成品|鬼服|没人|假联机|阉割|赶工",
}
