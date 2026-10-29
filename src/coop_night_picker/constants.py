REVIEW_TIERS = {
    9: {"en":"Overwhelmingly Positive","zh":"好评如潮","stars":"★★★★★","score":5.0},
    8: {"en":"Very Positive","zh":"特别好评","stars":"★★★★½","score":4.5},
    7: {"en":"Positive","zh":"好评","stars":"★★★★","score":4.0},
    6: {"en":"Mostly Positive","zh":"多半好评","stars":"★★★½","score":3.5},
    5: {"en":"Mixed","zh":"褒贬不一","stars":"★★★","score":3.0},
    4: {"en":"Mostly Negative","zh":"多半差评","stars":"★★½","score":2.5},
    3: {"en":"Negative","zh":"差评","stars":"★★","score":2.0},
    2: {"en":"Very Negative","zh":"特别差评","stars":"★½","score":1.5},
    1: {"en":"Overwhelmingly Negative","zh":"差评如潮","stars":"★","score":1.0},
    0: {"en":"No Enough Reviews","zh":"评测不足/试玩区","stars":"☆","score":0.0},
}
CATEGORY_ZH = {1:"多人",2:"单人",9:"合作",10:"游戏试玩(Demo)",20:"大型多人在线",24:"同屏/分屏",27:"跨平台多人",36:"在线PvP",37:"同屏/分屏PvP",38:"在线合作",39:"同屏/分屏合作",44:"远程同乐",47:"局域网PvP",48:"局域网合作",49:"玩家对战(PvP)"}
TAG_ZH = {1667:"恐怖",1721:"心理恐怖",3978:"生存恐怖",1685:"合作",3843:"在线合作",3841:"本地合作",4508:"合作战役",7368:"本地多人"}
HORROR_TAG_IDS={1667,1721,3978}
COOP_TAG_IDS={1685,3843,3841,4508,7368}
REVIEW_KEYWORDS={"coop_quality":r"联机|开黑|组队|队友|朋友|基友|双人|四人|多人|配合|欢乐|友尽","network":r"延迟|卡顿|掉线|加速器|网络|ping|断连|服务器|连不上|房主","session":r"一局|几小时|小时|通宵|一晚上|耐玩|流程|通关|快节奏","horror":r"吓人|恐怖|高能|jump\s*scare|跳脸|诡异|害怕|胆小|微恐","dealbreaker":r"退款|垃圾|坐牢|快逃|别买|外挂|挂壁|闪退|恶性bug|半成品|鬼服|没人|假联机"}
