#!/usr/bin/env python3
"""geointel — 周度决策备忘录生成器。

输入 questions.json（问题清单），逐条生成决策备忘录，跳过已存在的，
状态写回 questions.json.state.json。用于把「手动挑一个问题」变成「按清单跑」。

questions.json 格式：
  {"questions": [
    {"question": "...", "slug": "fed-hike-90d", "days": 30, "horizon": 90, "items": 40},
    ...
  ]}

用法：
  run-decide.sh --questions questions.json
"""
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import decide

QUESTIONS = [
    {
        "question": "未来 90 天美联储加息的概率与应对",
        "slug": "fed-hike-90d",
        "days": 30,
        "horizon": 90,
        "items": 40,
    },
    {
        "question": "未来 90 天半导体出口管制升级的概率与应对",
        "slug": "chips-export-control",
        "days": 30,
        "horizon": 90,
        "items": 40,
    },
    {
        "question": "未来 90 天中美关税谈判走向的概率与应对",
        "slug": "us-china-tariff-90d",
        "days": 30,
        "horizon": 90,
        "items": 40,
    },
    {
        "question": "未来 90 天全球 LNG 产能扩张对亚洲气价的影响",
        "slug": "lng-asia-90d",
        "days": 30,
        "horizon": 90,
        "items": 40,
    },
    {
        "question": "未来 90 天台积电在亚利桑那产能爬坡的确定性与风险",
        "slug": "tsmc-arizona-90d",
        "days": 30,
        "horizon": 90,
        "items": 40,
    },
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "questions.json")
    if not os.path.exists(path):
        json.dump({"questions": QUESTIONS}, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"created {path} with {len(QUESTIONS)} questions", flush=True)
        return 0
    return decide.run_questions(path)


if __name__ == "__main__":
    sys.exit(main())