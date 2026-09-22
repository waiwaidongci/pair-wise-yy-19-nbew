"""持久化与端到端（入口+判定+持久化）测试。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from petrography.decision import Event, Status
from petrography.entry import main
from petrography.persistence import EventStore, load_station

REPO_ROOT = Path(__file__).resolve().parents[1]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "nested" / "events.jsonl"
        self.store = EventStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_reload_roundtrip(self):
        events = [
            Event(1, "manifest_opened", "t1", {"no": "M1", "box_no": "A1"}),
            Event(2, "sample_cataloged", "t2",
                  {"manifest_no": "M1", "code": "S1", "depth": "100m", "sampled_on": "2026-09-20"}),
        ]
        for e in events:
            self.store.append(e)
        loaded = self.store.load()
        self.assertEqual([e.to_dict() for e in loaded], [e.to_dict() for e in events])
        # 日志只追加：历史行原文不变
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["payload"]["no"], "M1")

    def test_no_tmp_files_left_behind(self):
        self.store.append(Event(1, "manifest_opened", "t1", {"no": "M1", "box_no": "A1"}))
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_corrupt_line_reported(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('{"seq":1}\nnot-json\n', encoding="utf-8")
        with self.assertRaises(Exception):
            self.store.load()


class ReloadConsistencyTests(unittest.TestCase):
    """重载后总览、队列与履历吻合。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = str(Path(self.tmp.name) / "events.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, expect_ok=True):
        rc = main(["--store", self.store_path, *args])
        if expect_ok:
            self.assertEqual(rc, 0, msg=f"命令失败：{args}")
        else:
            self.assertNotEqual(rc, 0, msg=f"命令应失败：{args}")
        return rc

    def test_full_lifecycle_matches_after_reload(self):
        self.run_cli("open-manifest", "--no", "M1", "--box", "A1")
        self.run_cli("catalog", "--manifest", "M1", "--code", "S1",
                     "--depth", "100m", "--date", "2026-09-20")
        self.run_cli("catalog", "--manifest", "M1", "--code", "S2",
                     "--depth", "120m", "--date", "2026-09-20")
        # S1 正常放行
        self.run_cli("appraise", "--code", "S1", "--by", "张工",
                     "--mineral", "石英", "--texture", "粒状",
                     "--polarized", "正交偏光", "--porosity", "10")
        # S2 高孔隙 + 偏光缺失 -> 复查
        self.run_cli("appraise", "--code", "S2", "--by", "张工",
                     "--mineral", "方解石", "--texture", "泥晶",
                     "--porosity", "25")

        st1, _ = load_station(self.store_path)
        overview_before = st1.overview()
        queue_before = st1.queue()
        history_before = {c: st1.history(c) for c in ("S1", "S2")}

        # 同一复查者被拒
        self.run_cli("review", "--code", "S2", "--by", "张工",
                     "--verdict", "release", expect_ok=False)
        self.run_cli("review", "--code", "S2", "--by", "李工", "--verdict", "release")

        # 箱号冲突：无半成品（事件数不变）
        self.run_cli("open-manifest", "--no", "M2", "--box", "A1", expect_ok=False)

        # 空白编号：停工，无半成品
        self.run_cli("catalog", "--manifest", "M1", "--code", "",
                     "--depth", "1m", "--date", "2026-09-20", expect_ok=False)

        # 更正 S1：旧放行取消，重判进复查，历史稿保留
        self.run_cli("correct", "--code", "S1", "--by", "李工", "--porosity", "40")
        self.run_cli("review", "--code", "S1", "--by", "王工", "--verdict", "reject")
        # 再更正回合格现值
        self.run_cli("correct", "--code", "S1", "--by", "赵工",
                     "--porosity", "8", "--polarized", "单偏光")

        st2, _ = load_station(self.store_path)  # 模拟整体重载
        self.assertEqual(st2.overview(), {
            "manifests_total": 1,
            "manifests_open": 1,
            "boxes": 1,
            "samples_total": 2,
            "by_status": {
                "cataloged": 0,
                "released": 2,
                "review": 0,
                "rejected": 0,
            },
            "events": st2.overview()["events"],
        })
        self.assertEqual(st2.queue(), {"pending_appraisal": [], "pending_review": []})

        s1, s2 = st2.sample("S1"), st2.sample("S2")
        self.assertEqual(s1.status, Status.RELEASED)
        self.assertEqual(s1.appraisal.version, 3)
        self.assertEqual(s2.appraisal.version, 1)

        # 履历只读且完整：S1 经历 编目/初判/更正/复查驳回/更正
        kinds = [e["kind"] for e in st2.history("S1")]
        self.assertEqual(
            kinds,
            ["sample_cataloged", "appraised", "sample_corrected", "reviewed", "sample_corrected"],
        )
        first_draft = st2.history("S1")[1]
        self.assertEqual(first_draft["payload"]["decision"], "released")
        self.assertEqual(first_draft["payload"]["porosity_pct"], 10.0)

        # 重载前抓到的视图结构与重载后同形态（总览/队列/履历口径一致）
        self.assertEqual(set(overview_before.keys()), set(st2.overview().keys()))
        self.assertEqual(set(queue_before.keys()), set(st2.queue().keys()))
        self.assertEqual(
            [e["kind"] for e in history_before["S1"]],
            ["sample_cataloged", "appraised"],
        )

    def test_blank_field_does_not_touch_log(self):
        self.run_cli("open-manifest", "--no", "M1", "--box", "A1")
        text_after_open = Path(self.store_path).read_text(encoding="utf-8")
        self.run_cli("catalog", "--manifest", "M1", "--code", " ",
                     "--depth", "1m", "--date", "2026-09-20", expect_ok=False)
        self.assertEqual(Path(self.store_path).read_text(encoding="utf-8"), text_after_open)


class SubprocessCliTests(unittest.TestCase):
    """以子进程方式验证 python -m petrography 可独立运行。"""

    def test_help_and_json_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = str(Path(tmp) / "ev.jsonl")
            env = {"PYTHONPATH": str(REPO_ROOT)}
            import os
            env = {**os.environ, **env}
            r = subprocess.run(
                [sys.executable, "-m", "petrography", "--store", store,
                 "open-manifest", "--no", "M1", "--box", "B7"],
                capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run(
                [sys.executable, "-m", "petrography", "--store", store,
                 "--json", "overview"],
                capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["boxes"], 1)
            self.assertEqual(data["manifests_open"], 1)


if __name__ == "__main__":
    unittest.main()
