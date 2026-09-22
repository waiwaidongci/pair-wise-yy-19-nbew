"""持久化单元：事件日志的原子追加与重载。

设计要点：

- 只有追加，没有改写——“历史稿只读”由文件形态本身保证。
- 每次写入先写同目录临时文件再 ``os.replace`` 原子替换，进程中途失败
  不会留下半截单据（箱号冲突/空白停工时判定单元根本不产生事件，无半成品）。
- 判定单元不感知文件存在；本单元也不含任何业务规则。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .decision import Event, Station


class EventStore:
    """JSONL 仅追加事件存储。"""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def load(self) -> list[Event]:
        if not self.path.exists():
            return []
        events: list[Event] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(Event.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                    raise PersistenceError(
                        f"事件日志 {self.path} 第 {line_no} 行损坏：{exc}"
                    ) from None
        return events

    def append(self, event: Event) -> None:
        """原子追加单行事件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        # 临时文件与目标同目录，保证 os.replace 在同一文件系统上、原子可见
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                if self.path.exists():
                    with self.path.open("r", encoding="utf-8") as old:
                        tmp.write(old.read())
                tmp.write(line)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def append_many(self, events: list[Event]) -> None:
        """一次原子提交多条事件（当前每条命令恰好一条，保留扩展余地）。"""
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for e in events
        ]
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                if self.path.exists():
                    with self.path.open("r", encoding="utf-8") as old:
                        tmp.write(old.read())
                tmp.writelines(lines)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise


class PersistenceError(Exception):
    """日志文件无法读取或落盘时抛出。"""


def load_station(path: str | os.PathLike[str]) -> tuple[Station, EventStore]:
    """从事件日志重载台站：重放后总览、队列、履历均与停用时一致。"""
    store = EventStore(path)
    station = Station()
    station.replay(store.load())
    return station, store
