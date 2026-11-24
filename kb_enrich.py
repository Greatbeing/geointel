"""给参考术语补「常见误区」和「公式」。只填空字段，不覆盖已有内容。

两步走：
  python3 kb_enrich.py draft --groups statistics,derivatives   # 生成草稿 data/kb_enrich_drafts.jsonl
  python3 kb_enrich.py apply                                    # 人工抽查后写入数据库
"""
import argparse, json, os, re, sys

import store
import kb_refs

DRAFTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kb_enrich_drafts.jsonl")
FORMULA_GROUPS = {"probability", "statistics", "derivatives", "pricing", "valuation"}

SYSTEM = ("你是严谨的金融与统计学编辑。只输出一个 JSON 对象，不要任何解释或代码块标记。"
          "不确定就留空字符串，绝不编造。")


def prompt_for(ref, want_formula):
    return (
        "术语：%s（%s）\n定义：%s\n\n"
        "请输出 JSON：{\"caveat\": \"...\", \"formula\": \"...\"}\n"
        "- caveat：1-3 条中文「常见误区」，每条一句，用「；」分隔，指出实践中最常见的误用或误读，"
        "不要重复定义。\n"
        "- formula：%s\n"
    ) % (ref["title_zh"], ref["title_en"], ref["definition"],
         "该术语的标准公式，纯文本或 LaTeX，一行；没有公认公式则为空字符串"
         if want_formula else "固定为空字符串")


def parse_reply(text):
    """从模型回复里取出 JSON 对象；失败返回 None。"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    out = {}
    for k in ("caveat", "formula"):
        v = d.get(k)
        out[k] = v.strip() if isinstance(v, str) else ""
    return out


def targets(refs, groups):
    for r in refs:
        if r["layer"] != "term" or (groups and r["kind"] not in groups):
            continue
        need_cav = not r.get("caveat")
        need_f = not r.get("formula") and r["kind"] in FORMULA_GROUPS
        if need_cav or need_f:
            yield r, need_cav, need_f


def draft(groups, chat=None, limit=None, path=DRAFTS):
    if chat is None:
        import llm
        chat = llm.chat
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            done = {json.loads(l)["slug"] for l in f if l.strip()}
    n = 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r, need_cav, need_f in targets(kb_refs.all_refs(), groups):
            if r["slug"] in done:
                continue
            if limit is not None and n >= limit:
                break
            try:
                reply = chat(prompt_for(r, need_f), system=SYSTEM)
            except Exception as e:  # 单条失败不影响整体
                print("skip", r["slug"], e, file=sys.stderr)
                continue
            d = parse_reply(reply if isinstance(reply, str) else str(reply))
            if d is None:
                print("unparsable", r["slug"], file=sys.stderr)
                continue
            rec = {"slug": r["slug"], "title_zh": r["title_zh"],
                   "caveat": d["caveat"] if need_cav else "",
                   "formula": d["formula"] if need_f else ""}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


def apply(path=DRAFTS):
    """只填数据库中仍为空的字段。返回更新条数。"""
    if not os.path.exists(path):
        return 0
    con = kb_refs.connect()
    n = 0
    with con, open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            for k in ("caveat", "formula"):
                if d.get(k):
                    cur = con.execute(
                        "UPDATE kb_refs SET %s=? WHERE slug=? AND (%s IS NULL OR %s='')" % (k, k, k),
                        (d[k], d["slug"]))
                    n += cur.rowcount
    con.close()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft")
    d.add_argument("--groups", default="")
    d.add_argument("--limit", type=int)
    sub.add_parser("apply")
    a = ap.parse_args(argv)
    if a.cmd == "draft":
        groups = {g for g in a.groups.split(",") if g}
        print("drafted", draft(groups, limit=a.limit))
    else:
        print("applied", apply())


if __name__ == "__main__":
    main()
