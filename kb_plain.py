"""给术语写「大白话」解释（ljg-plain 红线）。单独存表，重新导入术语不会冲掉。

流程：写 -> 对照专业定义核对 -> 有错带着问题重写一次 -> 再核对。
  python3 kb_plain.py draft --groups statistics --limit 5   # 草稿 -> data/kb_plain_drafts.jsonl
  python3 kb_plain.py draft --redo t-derivatives-gamma      # 强制重写指定词条
  python3 kb_plain.py apply                                 # 抽查后写入 kb_plain 表
"""
import argparse, json, os, re, sys

# 审校用另一个模型：同一模型看不出自己的错
REVIEW_MODEL = os.environ.get("GEOINTEL_REVIEW_MODEL", "nvidia/nvidia/nemotron-3-ultra-550b-a55b")

import kb_refs

DRAFTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kb_plain_drafts.jsonl")
SCHEMA = "CREATE TABLE IF NOT EXISTS kb_plain (slug TEXT PRIMARY KEY, plain TEXT NOT NULL)"
MAX_LEN = 240

SYSTEM = (
    "你在给聪明的 12 岁孩子讲金融术语。只输出解释正文，不要标题、不要列表、不要引号。\n"
    "动笔前先想清楚：这个概念到底在量什么、因为什么而变。类比必须和这个机制一一对应，"
    "对不上就换类比，宁可不用类比也不要讲错。\n"
    "红线：\n"
    "1. 读出声像跟朋友聊天；只砍「此外」「值得注意的是」这类机械连词。\n"
    "2. 零术语：第一句不许出现术语名，先用大白话把意思落地，最后一句才顺带点出名字。"
    "中途不许冒出别的没解释过的术语。\n"
    "3. 短词优先，一句只说一件事。\n"
    "4. 具体：用看得见的东西和具体数字举例，全文只用一个类比，别中途换。\n"
    "5. 第一句就让人想读第二句，不铺垫。\n"
    "6. 不填充、不写金句、不写「打个比方」「简单来说」「记住」。\n"
    "7. 只解释，不给建议，不劝人买卖或远离。\n"
    "8. 正文不许出现英文单词或字母缩写（术语本身的缩写除外）。\n"
    "9. 类比在哪里不成立，可以用一句话点出；不要先说像、再说不像。\n"
    "10. 不确定就别说，绝不编造。\n"
    "总长 80-200 字，3-6 句。"
)

VERIFY_SYSTEM = ("你是严格的金融审校。只输出一个 JSON 对象："
                 "{\"ok\": true/false, \"problem\": \"...\"}。不要其他文字。")

BANNED = ("打个比方", "简单来说", "值得注意的是", "此外", "总而言之", "记住",
          "离它远点", "建议你", "**")


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


def facts(r):
    lines = ["术语：%s（%s）" % (r["title_zh"], r["title_en"]), "专业定义：%s" % r["definition"]]
    for k, label in (("formula", "公式"), ("why", "为什么重要"), ("caveat", "常见误区")):
        if r.get(k):
            lines.append("%s：%s" % (label, r[k]))
    return "\n".join(lines)


SECOND_ORDER = ("gamma", "convexity", "vanna", "volga", "charm")
NUMERIC_HINT = ("\n这是「变化的变化」类概念，不要用生活类比。直接用一组具体数字讲："
                "比如股价从100涨到101、再到102时，手里要对冲的股数各是多少、为什么会变。")


def prompt_for(r, problem="", previous=""):
    p = facts(r)
    if any(k in r["slug"] for k in SECOND_ORDER):
        p += NUMERIC_HINT
    if problem:
        p += "\n\n上一版：%s\n审校指出的问题：%s\n请重写，彻底修掉这个问题。" % (previous, problem)
    return p


def verify_prompt(r, text):
    return (facts(r) + "\n\n白话解释：" + text + "\n\n"
            "默认它有错，逐句找。检查：1) 每一句的说法是否在专业上成立（含因果方向、正负号、谁赚谁亏）；"
            "2) 类比的每个部件能否对应到概念里的具体东西，对不上就算错；"
            "3) 有没有和别的概念搞混；4) 有没有给投资建议；5) 有没有没解释的术语。"
            "只有全部过关才 ok=true；否则 ok=false，problem 用一句话说清最严重的错。")


def parse_verdict(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("ok"), bool):
        return None
    return d["ok"], str(d.get("problem") or "").strip()


def check(text, title="", title_en=""):
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
    allowed = set(re.findall(r"[A-Za-z]+", (title or "") + " " + (title_en or "")))
    eng = [w for w in re.findall(r"[A-Za-z]{2,}", t) if w not in allowed]
    if eng:
        issues.append("english:" + ",".join(sorted(set(eng))[:3]))
    first = re.split(r"[。？！?!]", t, maxsplit=1)[0]
    name = re.sub(r"\s+", "", title or "")
    if name and name in first:
        issues.append("name_in_first_sentence")
    return t, issues


def write_one(r, chat, review_model=None):
    """写 -> 用另一个模型核对 -> 必要时重写一次。返回 (文本, 问题列表)。"""
    rm = review_model or REVIEW_MODEL
    text = chat(prompt_for(r), system=SYSTEM)
    t, issues = check(str(text), r["title_zh"], r.get("title_en", ""))
    for attempt in range(2):
        try:
            v = parse_verdict(str(chat(verify_prompt(r, t), system=VERIFY_SYSTEM, model=rm)))
        except Exception:
            v = None
        problem = ""
        if v is None:
            problem = ""  # 审校回复无法解析，只看硬规则
        elif not v[0]:
            problem = v[1] or "机制不准确"
        if issues and not problem:
            problem = "违反硬规则：" + "，".join(issues)
        if not problem:
            return t, issues if v is not None else issues + ["unverified"]
        if attempt == 1:
            return t, issues + ["inaccurate:" + problem]
        text = chat(prompt_for(r, problem, t), system=SYSTEM)
        t, issues = check(str(text), r["title_zh"], r.get("title_en", ""))
    return t, issues


def draft(groups, chat=None, limit=None, redo=(), path=DRAFTS, review_model=None):
    if chat is None:
        import llm
        chat = llm.chat
    have = load()
    redo = set(redo)
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            done = {json.loads(l)["slug"] for l in f if l.strip()}
    done -= redo
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in kb_refs.all_refs():
            if r["layer"] != "term":
                continue
            if redo:
                if r["slug"] not in redo:
                    continue
            elif groups and r["kind"] not in groups:
                continue
            if r["slug"] in done or (r["slug"] in have and r["slug"] not in redo):
                continue
            if limit is not None and n >= limit:
                break
            try:
                t, issues = write_one(r, chat, review_model)
            except Exception as e:
                print("skip", r["slug"], e, file=sys.stderr)
                continue
            f.write(json.dumps({"slug": r["slug"], "title_zh": r["title_zh"],
                                "plain": t, "issues": issues}, ensure_ascii=False) + "\n")
            f.flush()
            n += 1
    return n


HARD = ("banned:", "english:", "empty", "too_long", "markup", "name_in_first")


def hard_ok(issues):
    return not any(str(i).startswith(HARD) for i in issues or [])


def apply(path=DRAFTS, force=()):
    """写入草稿：默认只收无问题的；force 里的只要过硬规则就写（人工放行审校误判）。"""
    force = set(force)
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
            ok = d["plain"] and (not d.get("issues") or
                                 (d["slug"] in force and hard_ok(d.get("issues"))))
            if ok:
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
    d.add_argument("--review-model", default="")
    a2 = sub.add_parser("apply")
    a2.add_argument("--force", default="", help="人工放行的 slug")
    a = ap.parse_args(argv)
    if a.cmd == "draft":
        print("drafted", draft({g for g in a.groups.split(",") if g}, limit=a.limit,
                               redo=[s for s in a.redo.split(",") if s],
                               review_model=a.review_model or None))
    else:
        print("applied", apply(force=[s for s in a.force.split(",") if s]))


if __name__ == "__main__":
    main()
