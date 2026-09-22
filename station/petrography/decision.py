"""判定单元：岩芯薄片编目、鉴定与放行的领域规则。

本模块是纯逻辑：不读写文件、不碰环境、不打印。所有状态变更都表达为
不可变的 :class:`Event`，由入口单元决定如何持久化，因此“历史稿只读”。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Optional

# 孔隙占比越过 20%（严格大于）即只能进入复查
PORE_LIMIT_PCT = 20.0


class Status(str, Enum):
    """样本当前所处的处置状态。"""

    CATALOGED = "cataloged"      # 已编目，待鉴定
    RELEASED = "released"        # 已放行
    REVIEW = "review"            # 复查中
    REJECTED = "rejected"        # 复查不通过，不予放行


class DecisionError(Exception):
    """业务规则被违反时抛出；消息可直接向值班人员展示。"""


@dataclass(frozen=True)
class Catalog:
    """样本的编目事实：编号、深度、采样日期。三项均不得空白。"""

    code: str
    depth: str
    sampled_on: str


@dataclass(frozen=True)
class Appraisal:
    """一稿鉴定结论。

    矿物组成、结构、偏光、孔隙占比为四项必填观察；
    偏光缺失（空串）或孔隙越过阈值时，该稿只能转复查。
    """

    mineral: str
    texture: str
    polarized: str
    porosity_pct: float
    observer: str
    decision: Status                       # RELEASED 或 REVIEW
    reason: str
    version: int = 1


@dataclass
class Sample:
    """一个薄片样本的现行状态。"""

    code: str
    depth: str
    sampled_on: str
    box_no: str
    manifest_no: str
    status: Status = Status.CATALOGED
    appraisal: Optional[Appraisal] = None

    def view(self) -> dict[str, Any]:
        """生成展示用快照（与持久化解耦，调用方可随意读取）。"""
        app = self.appraisal
        return {
            "code": self.code,
            "depth": self.depth,
            "sampled_on": self.sampled_on,
            "box_no": self.box_no,
            "manifest_no": self.manifest_no,
            "status": self.status.value,
            "appraisal_version": app.version if app else None,
            "mineral": app.mineral if app else "",
            "texture": app.texture if app else "",
            "polarized": app.polarized if app else "",
            "porosity_pct": app.porosity_pct if app else None,
            "observer": app.observer if app else "",
            "decision_reason": app.reason if app else "",
        }


@dataclass
class Manifest:
    """收纳箱的在途编目单据；一箱同时最多一张在途单据。"""

    no: str
    box_no: str
    open: bool = True
    codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Event:
    """不可变的历史事实。历史稿只读，任何更正都是追加新事件。"""

    seq: int
    kind: str
    at: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "at": self.at, "payload": self.payload}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Event":
        return Event(
            seq=int(data["seq"]),
            kind=str(data["kind"]),
            at=str(data["at"]),
            payload=dict(data["payload"]),
        )


def _require_nonblank(label: str, value: Optional[str]) -> str:
    """编号、深度、日期类字段：空白即停，拒绝补录。"""
    if value is None or not str(value).strip():
        raise DecisionError(f"{label}空白，已停止补录")
    return str(value).strip()


def judge_appraisal(
    mineral: str,
    texture: str,
    polarized: Optional[str],
    porosity_pct: float,
    observer: str,
) -> tuple[Status, str]:
    """按现值判定：放行还是只进复查。

    - 偏光观察缺失 -> 复查
    - 孔隙占比严格大于 20% -> 复查
    - 其余 -> 放行
    """
    polarized = (polarized or "").strip()
    if not polarized:
        return Status.REVIEW, "偏光缺失，转复查"
    if porosity_pct > PORE_LIMIT_PCT:
        return Status.REVIEW, f"孔隙占比{porosity_pct:g}%越过{PORE_LIMIT_PCT:g}%，转复查"
    return Status.RELEASED, "鉴定合格，放行"


class Station:
    """判定单元核心：持有现行状态，命令先校验后提交，绝不产生半成品。"""

    def __init__(self, clock: Any = None):
        self._samples: dict[str, Sample] = {}
        self._manifests: dict[str, Manifest] = {}
        self._open_by_box: dict[str, str] = {}
        self._events: list[Event] = []
        self._clock = clock

    # ------------------------------------------------------------------ 基础设施

    def _now(self) -> str:
        return self._clock() if self._clock else __import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ).isoformat()

    def _commit(self, kind: str, payload: dict[str, Any]) -> Event:
        """先构造事件（上面的校验全部通过才会走到这里），再一次性落状态。"""
        event = Event(seq=len(self._events) + 1, kind=kind, at=self._now(), payload=payload)
        self._apply(event)
        self._events.append(event)
        return event

    def _apply(self, event: Event) -> None:
        p = event.payload
        kind = event.kind
        if kind == "manifest_opened":
            m = Manifest(no=p["no"], box_no=p["box_no"])
            self._manifests[m.no] = m
            self._open_by_box[m.box_no] = m.no
        elif kind == "sample_cataloged":
            m = self._manifests[p["manifest_no"]]
            s = Sample(
                code=p["code"],
                depth=p["depth"],
                sampled_on=p["sampled_on"],
                box_no=m.box_no,
                manifest_no=m.no,
            )
            self._samples[s.code] = s
            m.codes.append(s.code)
        elif kind == "manifest_closed":
            m = self._manifests[p["no"]]
            m.open = False
            self._open_by_box.pop(m.box_no, None)
        elif kind == "appraised":
            s = self._samples[p["code"]]
            s.appraisal = Appraisal(
                mineral=p["mineral"],
                texture=p["texture"],
                polarized=p["polarized"],
                porosity_pct=float(p["porosity_pct"]),
                observer=p["observer"],
                decision=Status(p["decision"]),
                reason=p["reason"],
                version=int(p.get("version", 1)),
            )
            s.status = Status(p["decision"])
        elif kind == "reviewed":
            self._samples[p["code"]].status = Status(p["decision"])
        elif kind == "sample_corrected":
            s = self._samples[p["code"]]
            if p.get("depth") is not None:
                s.depth = p["depth"]
            if p.get("sampled_on") is not None:
                s.sampled_on = p["sampled_on"]
            if p.get("appraisal") is not None:
                a = p["appraisal"]
                s.appraisal = Appraisal(
                    mineral=a["mineral"],
                    texture=a["texture"],
                    polarized=a["polarized"],
                    porosity_pct=float(a["porosity_pct"]),
                    observer=a["observer"],
                    decision=Status(a["decision"]),
                    reason=a["reason"],
                    version=int(a["version"]),
                )
                s.status = Status(a["decision"])
        else:
            raise DecisionError(f"未知事件类型：{kind}")

    def replay(self, events: list[Event]) -> None:
        """持久化单元重载后，用事件日志重建全部状态。"""
        for event in sorted(events, key=lambda e: e.seq):
            self._apply(event)
            self._events.append(event)

    # ------------------------------------------------------------------ 编目命令

    def open_manifest(self, manifest_no: str, box_no: str) -> Event:
        """为收纳箱开立在途单据。箱号冲突（已有在途单据）则拒绝，无半成品。"""
        no = _require_nonblank("单据号", manifest_no)
        box = _require_nonblank("箱号", box_no)
        if no in self._manifests:
            raise DecisionError(f"单据号 {no} 已存在")
        existing = self._open_by_box.get(box)
        if existing is not None:
            raise DecisionError(f"箱号 {box} 已有在途单据 {existing}，不得另开")
        return self._commit("manifest_opened", {"no": no, "box_no": box})

    def catalog_sample(
        self, manifest_no: str, code: str, depth: str, sampled_on: str
    ) -> Event:
        """把薄片补录到在途单据。编号/深度/日期任一空白即停。"""
        no = _require_nonblank("单据号", manifest_no)
        c = _require_nonblank("编号", code)
        d = _require_nonblank("深度", depth)
        date = _require_nonblank("采样日期", sampled_on)
        m = self._manifests.get(no)
        if m is None:
            raise DecisionError(f"单据 {no} 不存在")
        if not m.open:
            raise DecisionError(f"单据 {no} 已封箱，不能再补录")
        if c in self._samples:
            raise DecisionError(f"编号 {c} 已编目，不得重复")
        return self._commit(
            "sample_cataloged",
            {"manifest_no": no, "code": c, "depth": d, "sampled_on": date},
        )

    def close_manifest(self, manifest_no: str) -> Event:
        """封箱：单据不再在途，箱号释放。"""
        no = _require_nonblank("单据号", manifest_no)
        m = self._manifests.get(no)
        if m is None:
            raise DecisionError(f"单据 {no} 不存在")
        if not m.open:
            raise DecisionError(f"单据 {no} 已封箱")
        return self._commit("manifest_closed", {"no": no})

    # ------------------------------------------------------------------ 鉴定命令

    def appraise(
        self,
        code: str,
        observer: str,
        mineral: str,
        texture: str,
        polarized: str,
        porosity_pct: float,
    ) -> Event:
        """首次鉴定：写入矿物组成、结构、偏光与孔隙占比，按现值判定。"""
        c = _require_nonblank("编号", code)
        who = _require_nonblank("鉴定者", observer)
        mins = _require_nonblank("矿物组成", mineral)
        tex = _require_nonblank("结构", texture)
        pol = (polarized or "").strip()  # 偏光允许缺失：缺失即只进复查
        try:
            pore = float(porosity_pct)
        except (TypeError, ValueError):
            raise DecisionError("孔隙占比必须是 0–100 的百分数") from None
        if not 0 <= pore <= 100:
            raise DecisionError("孔隙占比必须落在 0–100 之间")
        s = self._samples.get(c)
        if s is None:
            raise DecisionError(f"样本 {c} 不存在")
        if s.appraisal is not None:
            raise DecisionError(f"样本 {c} 已有鉴定稿，更正请走 sample_corrected")
        decision, reason = judge_appraisal(mins, tex, pol, pore, who)
        return self._commit(
            "appraised",
            {
                "code": c,
                "observer": who,
                "mineral": mins,
                "texture": tex,
                "polarized": pol,
                "porosity_pct": pore,
                "decision": decision.value,
                "reason": reason,
                "version": 1,
            },
        )

    def review(self, code: str, reviewer: str, passed: bool) -> Event:
        """复查放行/驳回。复查者必须与首次鉴定者不同。"""
        c = _require_nonblank("编号", code)
        who = _require_nonblank("复查者", reviewer)
        s = self._samples.get(c)
        if s is None:
            raise DecisionError(f"样本 {c} 不存在")
        if s.appraisal is None or s.status != Status.REVIEW:
            raise DecisionError(f"样本 {c} 当前不在复查队列")
        if who == s.appraisal.observer:
            raise DecisionError("复查者必须与首次鉴定者不同")
        decision = Status.RELEASED if passed else Status.REJECTED
        reason = "复查通过，放行" if passed else "复查不通过，驳回"
        return self._commit(
            "reviewed",
            {"code": c, "reviewer": who, "decision": decision.value, "reason": reason},
        )

    # ------------------------------------------------------------------ 更正命令

    def correct_sample(
        self,
        code: str,
        corrector: str,
        depth: Optional[str] = None,
        sampled_on: Optional[str] = None,
        mineral: Optional[str] = None,
        texture: Optional[str] = None,
        polarized: Optional[str] = None,
        porosity_pct: Optional[float] = None,
    ) -> Event:
        """样本更正：旧放行立即取消，按现值重判；历史稿只读，另立新版本。

        只改深度/日期：仅更新编目事实。涉及任一鉴定字段则整体重写鉴定稿，
        未提供的鉴定字段沿用现值（偏光沿用现值时也可能仍为空）。
        """
        c = _require_nonblank("编号", code)
        who = _require_nonblank("更正者", corrector)
        s = self._samples.get(c)
        if s is None:
            raise DecisionError(f"样本 {c} 不存在")

        new_depth = _require_nonblank("深度", depth) if depth is not None else None
        new_date = _require_nonblank("采样日期", sampled_on) if sampled_on is not None else None

        appraisal_keys = (mineral, texture, polarized, porosity_pct)
        touches_appraisal = any(v is not None for v in appraisal_keys)

        payload: dict[str, Any] = {"code": c, "corrector": who}
        if new_depth is not None:
            payload["depth"] = new_depth
        if new_date is not None:
            payload["sampled_on"] = new_date

        if touches_appraisal:
            if s.appraisal is None:
                raise DecisionError(f"样本 {c} 尚未鉴定，不能更正鉴定项，仅可更正深度/日期")
            old = s.appraisal
            mins = _require_nonblank("矿物组成", mineral if mineral is not None else old.mineral)
            tex = _require_nonblank("结构", texture if texture is not None else old.texture)
            pol = (
                polarized.strip()
                if polarized is not None
                else old.polarized
            )
            if porosity_pct is not None:
                try:
                    pore = float(porosity_pct)
                except (TypeError, ValueError):
                    raise DecisionError("孔隙占比必须是 0–100 的百分数") from None
                if not 0 <= pore <= 100:
                    raise DecisionError("孔隙占比必须落在 0–100 之间")
            else:
                pore = old.porosity_pct
            # 更正者即新稿鉴定者；旧放行取消，按现值重新判定
            decision, reason = judge_appraisal(mins, tex, pol, pore, who)
            if s.status == Status.RELEASED:
                reason = "旧放行已取消，按现值重判：" + reason
            payload["appraisal"] = {
                "mineral": mins,
                "texture": tex,
                "polarized": pol,
                "porosity_pct": pore,
                "observer": who,
                "decision": decision.value,
                "reason": reason,
                "version": old.version + 1,
            }
        elif s.status == Status.RELEASED:
            # 仅编目事实更正也使旧放行失效：无鉴定变化，按现值重判仍为放行，
            # 但要留下“取消后重判”的新稿痕迹。
            old = s.appraisal
            decision, reason = judge_appraisal(
                old.mineral, old.texture, old.polarized, old.porosity_pct, who
            )
            payload["appraisal"] = {
                "mineral": old.mineral,
                "texture": old.texture,
                "polarized": old.polarized,
                "porosity_pct": old.porosity_pct,
                "observer": who,
                "decision": decision.value,
                "reason": "旧放行已取消，按现值重判：" + reason,
                "version": old.version + 1,
            }

        return self._commit("sample_corrected", payload)

    # ------------------------------------------------------------------ 只读视图

    def overview(self) -> dict[str, Any]:
        """总览：箱/单据、样本量与各处置状态计数。"""
        by_status = {st.value: 0 for st in Status}
        for s in self._samples.values():
            by_status[s.status.value] += 1
        return {
            "manifests_total": len(self._manifests),
            "manifests_open": len(self._open_by_box),
            "boxes": len({m.box_no for m in self._manifests.values()}),
            "samples_total": len(self._samples),
            "by_status": by_status,
            "events": len(self._events),
        }

    def queue(self) -> dict[str, list[dict[str, Any]]]:
        """待办队列：待鉴定、待复查（含复查所需的原鉴定者）。"""
        pending_appraisal: list[dict[str, Any]] = []
        pending_review: list[dict[str, Any]] = []
        for s in self._samples.values():
            view = s.view()
            if s.status == Status.CATALOGED:
                pending_appraisal.append(view)
            elif s.status == Status.REVIEW:
                view["first_observer"] = s.appraisal.observer if s.appraisal else ""
                pending_review.append(view)
        return {
            "pending_appraisal": pending_appraisal,
            "pending_review": pending_review,
        }

    def history(self, code: str) -> list[dict[str, Any]]:
        """样本履历：只返回事件深拷贝，调用方无法改写历史稿。"""
        import copy

        return [copy.deepcopy(e.to_dict()) for e in self._events if e.payload.get("code") == code]

    def ledger(self) -> list[dict[str, Any]]:
        """全本只读履历（持久化自检/导出用）。"""
        import copy

        return [copy.deepcopy(e.to_dict()) for e in self._events]

    def sample(self, code: str) -> Sample:
        s = self._samples.get(code)
        if s is None:
            raise DecisionError(f"样本 {code} 不存在")
        return replace(s)
