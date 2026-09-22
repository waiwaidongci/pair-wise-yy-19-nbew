"""入口单元：命令行交互、展示与用例编排。

本单元只做三件事：解析参数 -> 调用判定单元 -> 经持久化单元落盘。
业务规则一律不在此实现。

用法示例：
    python -m petrography open-manifest --no BD-2026-001 --box A-07
    python -m petrography catalog --manifest BD-2026-001 \\
        --code XB-17-03 --depth 1287.4m --date 2026-09-20
    python -m petrography appraise --code XB-17-03 --by 张工 \\
        --mineral 石英、斜长石 --texture 半自形粒状 \\
        --polarized 正交偏光 --porosity 12.5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .decision import DecisionError, Event, Station, Status
from .persistence import EventStore, PersistenceError, load_station

DEFAULT_STORE = str(Path(__file__).resolve().parent.parent / "data" / "events.jsonl")

STATUS_LABEL = {
    Status.CATALOGED: "待鉴定",
    Status.RELEASED: "已放行",
    Status.REVIEW: "待复查",
    Status.REJECTED: "已驳回",
}

EVENT_LABEL = {
    "manifest_opened": "开立在途单据",
    "sample_cataloged": "编目补录",
    "manifest_closed": "封箱",
    "appraised": "首次鉴定",
    "reviewed": "复查结论",
    "sample_corrected": "样本更正",
}


# ---------------------------------------------------------------------- 展示辅助

def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _print_overview(o: dict[str, Any]) -> None:
    print("总览")
    print(f"  收纳箱：{o['boxes']}    在途单据：{o['manifests_open']}/{o['manifests_total']}")
    print(f"  样本总数：{o['samples_total']}    履历事件：{o['events']}")
    print(
        "  状态分布："
        f"待鉴定 {o['by_status']['cataloged']}，"
        f"待复查 {o['by_status']['review']}，"
        f"已放行 {o['by_status']['released']}，"
        f"已驳回 {o['by_status']['rejected']}"
    )


def _print_sample_row(v: dict[str, Any]) -> None:
    pore = "—" if v["porosity_pct"] is None else f"{v['porosity_pct']:g}%"
    extra = f"    鉴定者：{v['observer']}" if v["observer"] else ""
    print(
        f"  {v['code']}  箱 {v['box_no']} / 单 {v['manifest_no']}  "
        f"深 {v['depth']}  采于 {v['sampled_on']}  "
        f"[{STATUS_LABEL[Status(v['status'])]}]"
    )
    if v["appraisal_version"] is not None:
        print(
            f"      矿物：{v['mineral']}；结构：{v['texture']}；"
            f"偏光：{v['polarized'] or '缺失'}；孔隙：{pore}；"
            f"稿次 v{v['appraisal_version']}{extra}"
        )
        print(f"      判定依据：{v['decision_reason']}")


def _print_queue(q: dict[str, list[dict[str, Any]]]) -> None:
    print("待鉴定队列")
    if q["pending_appraisal"]:
        for v in q["pending_appraisal"]:
            _print_sample_row(v)
    else:
        print("  （空）")
    print("待复查队列（复查者须不同于首次鉴定者）")
    if q["pending_review"]:
        for v in q["pending_review"]:
            _print_sample_row(v)
            print(f"      → 须由非「{v['first_observer']}」的人员复查")
    else:
        print("  （空）")


def _print_history(code: str, rows: list[dict[str, Any]]) -> None:
    print(f"样本 {code} 履历（只读历史稿）")
    if not rows:
        print("  （无记录）")
        return
    for e in rows:
        p = e["payload"]
        head = f"  #{e['seq']} {e['at']}  {EVENT_LABEL.get(e['kind'], e['kind'])}"
        print(head)
        if e["kind"] == "sample_cataloged":
            print(f"      深度 {p['depth']}，采样日期 {p['sampled_on']}，单据 {p['manifest_no']}")
        elif e["kind"] == "appraised":
            print(
                f"      {p['observer']}：{p['mineral']} / {p['texture']} / "
                f"偏光 {p['polarized'] or '缺失'} / 孔隙 {p['porosity_pct']:g}%"
            )
            print(f"      判定 v{p['version']}：{STATUS_LABEL[Status(p['decision'])]}（{p['reason']}）")
        elif e["kind"] == "reviewed":
            print(
                f"      复查者 {p['reviewer']}：{STATUS_LABEL[Status(p['decision'])]}"
                f"（{p['reason']}）"
            )
        elif e["kind"] == "sample_corrected":
            print(f"      更正者：{p['corrector']}")
            if p.get("depth") or p.get("sampled_on"):
                print(f"      编目更正：深度 {p.get('depth', '—')}，日期 {p.get('sampled_on', '—')}")
            if p.get("appraisal"):
                a = p["appraisal"]
                print(
                    f"      重判 v{a['version']}：{a['mineral']} / {a['texture']} / "
                    f"偏光 {a['polarized'] or '缺失'} / 孔隙 {a['porosity_pct']:g}%"
                )
                print(f"      → {STATUS_LABEL[Status(a['decision'])]}（{a['reason']}）")


def _print_confirmation(event: Event) -> None:
    p = event.payload
    label = EVENT_LABEL.get(event.kind, event.kind)
    if event.kind == "manifest_opened":
        detail = f"单据 {p['no']} 已开立，占用箱号 {p['box_no']}"
    elif event.kind == "sample_cataloged":
        detail = f"编号 {p['code']} 已补录至单据 {p['manifest_no']}"
    elif event.kind == "manifest_closed":
        detail = f"单据 {p['no']} 已封箱，箱号已释放"
    elif event.kind == "appraised":
        detail = (
            f"编号 {p['code']}：{STATUS_LABEL[Status(p['decision'])]}（{p['reason']}）"
        )
    elif event.kind == "reviewed":
        detail = f"编号 {p['code']}：{STATUS_LABEL[Status(p['decision'])]}（{p['reason']}）"
    else:
        a = p.get("appraisal")
        tail = ""
        if a:
            tail = f" → {STATUS_LABEL[Status(a['decision'])]}（v{a['version']}）"
        detail = f"编号 {p['code']} 已更正{tail}"
    print(f"#{event.seq} {label}：{detail}")


# ---------------------------------------------------------------------- 命令实现

def _mutate(store_path: str, fn: Callable[[Station], Event], as_json: bool) -> None:
    """加载台站 -> 执行判定命令 -> 原子落盘。失败时不写任何字节。"""
    station, store = load_station(store_path)
    event = fn(station)
    store.append(event)
    if as_json:
        _print_json(event.to_dict())
    else:
        _print_confirmation(event)


def cmd_open_manifest(args: argparse.Namespace, store_path: str) -> None:
    _mutate(
        store_path,
        lambda st: st.open_manifest(args.no, args.box),
        args.json,
    )


def cmd_catalog(args: argparse.Namespace, store_path: str) -> None:
    _mutate(
        store_path,
        lambda st: st.catalog_sample(args.manifest, args.code, args.depth, args.date),
        args.json,
    )


def cmd_close_manifest(args: argparse.Namespace, store_path: str) -> None:
    _mutate(store_path, lambda st: st.close_manifest(args.no), args.json)


def cmd_appraise(args: argparse.Namespace, store_path: str) -> None:
    _mutate(
        store_path,
        lambda st: st.appraise(
            args.code,
            args.by,
            args.mineral,
            args.texture,
            args.polarized if args.polarized is not None else "",
            args.porosity,
        ),
        args.json,
    )


def cmd_review(args: argparse.Namespace, store_path: str) -> None:
    _mutate(
        store_path,
        lambda st: st.review(args.code, args.by, passed=args.verdict == "release"),
        args.json,
    )


def cmd_correct(args: argparse.Namespace, store_path: str) -> None:
    _mutate(
        store_path,
        lambda st: st.correct_sample(
            args.code,
            args.by,
            depth=args.depth,
            sampled_on=args.date,
            mineral=args.mineral,
            texture=args.texture,
            polarized=args.polarized,
            porosity_pct=args.porosity,
        ),
        args.json,
    )


def cmd_overview(args: argparse.Namespace, store_path: str) -> None:
    station, _ = load_station(store_path)
    o = station.overview()
    _print_json(o) if args.json else _print_overview(o)


def cmd_queue(args: argparse.Namespace, store_path: str) -> None:
    station, _ = load_station(store_path)
    q = station.queue()
    _print_json(q) if args.json else _print_queue(q)


def cmd_history(args: argparse.Namespace, store_path: str) -> None:
    station, _ = load_station(store_path)
    # 先让编号不存在的错误在判定单元抛出
    station.sample(args.code)
    rows = station.history(args.code)
    _print_json(rows) if args.json else _print_history(args.code, rows)


# ---------------------------------------------------------------------- 参数解析

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="petrography",
        description="岩芯薄片显微样本编目与鉴定放行台",
    )
    parser.add_argument(
        "--store",
        default=DEFAULT_STORE,
        help=f"事件日志路径（默认 {DEFAULT_STORE}）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于对账")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open-manifest", help="为收纳箱开立在途单据（一箱一张）")
    p.add_argument("--no", required=True, help="单据号")
    p.add_argument("--box", required=True, help="收纳箱箱号")
    p.set_defaults(func=cmd_open_manifest)

    p = sub.add_parser("catalog", help="向在途单据补录样本（编号/深度/日期空白即停）")
    p.add_argument("--manifest", required=True, help="在途单据号")
    p.add_argument("--code", required=True, help="样本编号")
    p.add_argument("--depth", required=True, help="取样深度")
    p.add_argument("--date", required=True, help="采样日期 YYYY-MM-DD")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("close-manifest", help="封箱，单据不再在途")
    p.add_argument("--no", required=True, help="单据号")
    p.set_defaults(func=cmd_close_manifest)

    p = sub.add_parser("appraise", help="首次鉴定：矿物/结构/偏光/孔隙占比")
    p.add_argument("--code", required=True, help="样本编号")
    p.add_argument("--by", required=True, help="鉴定者署名")
    p.add_argument("--mineral", required=True, help="矿物组成")
    p.add_argument("--texture", required=True, help="结构")
    p.add_argument(
        "--polarized",
        default=None,
        help="偏光观察；缺省即视为偏光缺失，只进复查",
    )
    p.add_argument("--porosity", required=True, type=float, help="孔隙占比（百分数 0–100）")
    p.set_defaults(func=cmd_appraise)

    p = sub.add_parser("review", help="复查放行或驳回（复查者须不同于首次鉴定者）")
    p.add_argument("--code", required=True, help="样本编号")
    p.add_argument("--by", required=True, help="复查者署名")
    p.add_argument(
        "--verdict",
        required=True,
        choices=["release", "reject"],
        help="release=复查放行，reject=驳回",
    )
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("correct", help="样本更正：旧放行取消，按现值重判，历史稿只读")
    p.add_argument("--code", required=True, help="样本编号")
    p.add_argument("--by", required=True, help="更正者署名（即新稿鉴定者）")
    p.add_argument("--depth", default=None, help="更正后的深度")
    p.add_argument("--date", default=None, help="更正后的采样日期")
    p.add_argument("--mineral", default=None, help="更正后的矿物组成")
    p.add_argument("--texture", default=None, help="更正后的结构")
    p.add_argument("--polarized", default=None, help="更正后的偏光观察")
    p.add_argument("--porosity", default=None, type=float, help="更正后的孔隙占比")
    p.set_defaults(func=cmd_correct)

    p = sub.add_parser("overview", help="总览")
    p.set_defaults(func=cmd_overview)

    p = sub.add_parser("queue", help="待鉴定 / 待复查队列")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("history", help="样本履历（只读）")
    p.add_argument("--code", required=True, help="样本编号")
    p.set_defaults(func=cmd_history)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args, args.store)
    except (DecisionError, PersistenceError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
