"""给术语写「大白话」解释（ljg-plain 红线）。单独存表，重新导入术语不会冲掉。

  python3 kb_plain.py draft --groups statistics --limit 5   # 草稿 -> data/kb_plain_drafts.jsonl
  python3 kb_plain.py apply                                 # 抽查后写入 kb_plain 表
"""
import argparse, json, os, re, sys

import kb_refs

DRAFTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kb_plain_drafts.jsonl")
SCHEMA = "CREATE TABLE IF NOT EXISTS kb_plain (slug TEXT PRIMARY KEY, plain TEXT NOT NULL)"
MAX_LEN = 260

SYSTEM = (
    "你在给聪明的 12 岁孩子讲金融术语。只输出解释正文，不要标题、不要列表、不要引号。\n"
    "红线：\n"
    "1. 读出声像跟朋友聊天；只砍「此外」「值得注意的是」这类机械连词。\n"
    "2. 零术语：先用大白话把意思落地，最后才顺带提术语名。\n"
    "3. 短词优先，一句只说一件事，长句拆短。\n"
    "4. 具体：用看得见的东西举例（小卖部、雨伞、房租、张三借钱），给具体数字。\n"
    "5. 第一句就让人想读第二句，不铺垫不背景。\n"
    "6. 不填充、不软化、不写金句、不写「打个比方」「简单来说」。\n"
    "7. 类比要承重；如果类比在某处不成立，用一句话点出那道裂缝。\n"
    "8. 不确定就别说，绝不编造。\n"
    "总长 80-220 字，3-6 句。"
)

BANNED = ("打个比方", "简单来说", "值得注意的是", "此外", "总而言之", "**")


def ensure(con):
    con.execute(SCHEMA)


def load(con=None):
    own = con is None
    con = con or kb_refs.connect()
    ensure(con)
    d = dict(con.execute("SELECT slug, plain FROM kb_plain").fetchall())
    if own:
        con.close()
    return d


def prompt_for(r):
    return "术语：%s（%s）\n专业定义：%s\n%s" % (
        r["title_zh"], r["title_en"], r["definition"],
        ("补充：%s" % r["why"]) if r.get("why") else "")


def check(text):
    """返回 (清洗后文本, 问题列表)。"""
    t = re.sub(r"\s+", "", (text or "").strip().strip('"“”'))
    issues = []
    if not t:
        issues.append("empty")
    if len(t) > MAX_LEN:
        issues.append("too_long:%d" % len(t))
    issues += ["banned:" + b for b in BANNED if b in t]
    if re.search(r"^#|^\*|\n-", text or ""):
        issues.append("markup")
    return t, issues


def draft(groups, chat=None, limit=None, redo=(), path=DRAFTS):
    if chat is None:
        import llm
        chat = llm.chat
    have = load()
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            done = {json.loads(l)["slug"] for l in f if l.strip()}
    done -= set(redo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in kb_refs.all_refs():
            if r["layer"] != "term" or (groups and r["kind"] not in groups):
                continue
            if r["slug"] in done or (r["slug"] in have and r["slug"] not in redo):
                continue
            if limit is not None and n >= limit:
                break
            try:
                reply = chat(prompt_for(r), system=SYSTEM)
            except Exception as e:
                print("skip", r["slug"], e, file=sys.stderr)
                continue
            t, issues = check(reply if isinstance(reply, str) else str(reply))
            f.write(json.dumps({"slug": r["slug"], "title_zh": r["title_zh"],
                                "plain": t, "issues": issues}, ensure_ascii=False) + "\n")
            n += 1
    return n


def apply(path=DRAFTS):
    """写入无问题的草稿（后写覆盖先写）。返回写入条数。"""
    if not os.path.exists(path):
        return 0
    latest = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                latest[d["slug"]] = d
    con = kb_refs.connect()
    ensure(con)
    n = 0
    with con:
        for d in latest.values():
            if d["plain"] and not d.get("issues"):
                con.execute("INSERT OR REPLACE INTO kb_plain(slug, plain) VALUES(?,?)",
                            (d["slug"], d["plain"]))
                n += 1
    con.close()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft")
    d.add_argument("--groups", default="")
    d.add_argument("--limit", type=int)
    d.add_argument("--redo", default="", help="逗号分隔的 slug，强制重写")
    sub.add_parser("apply")
    a = ap.parse_args(argv)
    if a.cmd == "draft":
        print("drafted", draft({g for g in a.groups.split(",") if g}, limit=a.limit,
                               redo=[s for s in a.redo.split(",") if s]))
    else:
        print("applied", apply())


if __name__ == "__main__":
    main()
