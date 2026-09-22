"""判定单元测试。"""

import unittest

from petrography.decision import DecisionError, Status, Station


def clock_factory(prefix="t"):
    n = {"i": 0}

    def tick():
        n["i"] += 1
        return f"{prefix}-{n['i']}"

    return tick


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.st = Station(clock=clock_factory())

    def open_manifest(self, no="M1", box="A1"):
        return self.st.open_manifest(no, box)

    def test_open_manifest_ok(self):
        e = self.open_manifest()
        self.assertEqual(e.kind, "manifest_opened")
        self.assertEqual(e.seq, 1)
        self.assertEqual(self.st.overview()["manifests_open"], 1)

    def test_box_conflict_produces_no_half_product(self):
        self.open_manifest("M1", "A1")
        with self.assertRaises(DecisionError):
            self.st.open_manifest("M2", "A1")
        # 冲突后：没有新单据、没有新事件、箱仍只被 M1 占用
        self.assertEqual(self.st.overview()["manifests_total"], 1)
        self.assertEqual(self.st.overview()["events"], 1)
        self.assertEqual(self.st.overview()["manifests_open"], 1)
        # 箱号释放（封箱）后可重新占用
        self.st.close_manifest("M1")
        self.st.open_manifest("M2", "A1")
        self.assertEqual(self.st.overview()["manifests_open"], 1)

    def test_blank_code_depth_or_date_stops_entry(self):
        self.open_manifest()
        for kwargs in (
            dict(manifest_no="M1", code="", depth="10m", sampled_on="2026-09-20"),
            dict(manifest_no="M1", code="S1", depth="   ", sampled_on="2026-09-20"),
            dict(manifest_no="M1", code="S1", depth="10m", sampled_on=""),
            dict(manifest_no="", code="S1", depth="10m", sampled_on="2026-09-20"),
        ):
            with self.assertRaises(DecisionError):
                self.st.catalog_sample(**kwargs)
        self.assertEqual(self.st.overview()["samples_total"], 0)

    def test_closed_manifest_rejects_entry_and_duplicate_code(self):
        self.open_manifest()
        self.st.catalog_sample("M1", "S1", "10m", "2026-09-20")
        with self.assertRaises(DecisionError):
            self.st.catalog_sample("M1", "S1", "11m", "2026-09-20")
        self.st.close_manifest("M1")
        with self.assertRaises(DecisionError):
            self.st.catalog_sample("M1", "S2", "11m", "2026-09-20")
        self.assertEqual(self.st.overview()["samples_total"], 1)


class AppraisalTests(unittest.TestCase):
    def setUp(self):
        self.st = Station(clock=clock_factory())
        self.st.open_manifest("M1", "A1")
        self.st.catalog_sample("M1", "S1", "100m", "2026-09-20")
        self.st.catalog_sample("M1", "S2", "110m", "2026-09-20")
        self.st.catalog_sample("M1", "S3", "120m", "2026-09-20")
        self.st.catalog_sample("M1", "S4", "130m", "2026-09-20")

    def appraise(self, code, **kw):
        kw.setdefault("observer", "张工")
        kw.setdefault("mineral", "石英、斜长石")
        kw.setdefault("texture", "半自形粒状")
        kw.setdefault("polarized", "正交偏光")
        kw.setdefault("porosity_pct", 10.0)
        return self.st.appraise(code=code, **kw)

    def test_normal_appraisal_releases(self):
        e = self.appraise("S1")
        self.assertEqual(e.payload["decision"], Status.RELEASED.value)
        self.assertEqual(self.st.sample("S1").status, Status.RELEASED)

    def test_porosity_at_limit_releases_above_limit_reviews(self):
        e = self.appraise("S1", porosity_pct=20.0)
        self.assertEqual(e.payload["decision"], Status.RELEASED.value)
        e = self.appraise("S2", porosity_pct=20.1)
        self.assertEqual(e.payload["decision"], Status.REVIEW.value)
        self.assertEqual(self.st.sample("S2").status, Status.REVIEW)

    def test_missing_polarized_only_goes_to_review(self):
        e = self.appraise("S3", polarized="")
        self.assertEqual(e.payload["decision"], Status.REVIEW.value)
        self.assertIn("偏光缺失", e.payload["reason"])

    def test_appraisal_fields_nonblank_and_porosity_range(self):
        with self.assertRaises(DecisionError):
            self.appraise("S4", mineral=" ")
        with self.assertRaises(DecisionError):
            self.appraise("S4", texture="")
        with self.assertRaises(DecisionError):
            self.appraise("S4", porosity_pct=101)
        # 校验失败不产生半成品
        self.assertIsNone(self.st.sample("S4").appraisal)
        self.assertEqual(self.st.sample("S4").status, Status.CATALOGED)

    def test_double_appraisal_must_use_correction(self):
        self.appraise("S1")
        with self.assertRaises(DecisionError):
            self.appraise("S1", porosity_pct=5.0)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.st = Station(clock=clock_factory())
        self.st.open_manifest("M1", "A1")
        self.st.catalog_sample("M1", "S1", "100m", "2026-09-20")
        self.st.appraise("S1", "张工", "石英", "粒状", "", 25.0)
        self.assertEqual(self.st.sample("S1").status, Status.REVIEW)

    def test_reviewer_must_differ_from_first_observer(self):
        with self.assertRaises(DecisionError):
            self.st.review("S1", "张工", passed=True)
        self.assertEqual(self.st.sample("S1").status, Status.REVIEW)

    def test_different_reviewer_can_release(self):
        self.st.review("S1", "李工", passed=True)
        self.assertEqual(self.st.sample("S1").status, Status.RELEASED)

    def test_reject_path(self):
        self.st.review("S1", "李工", passed=False)
        self.assertEqual(self.st.sample("S1").status, Status.REJECTED)
        # 已驳回样本不在复查队列，不能再次复查
        with self.assertRaises(DecisionError):
            self.st.review("S1", "王工", passed=True)


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.st = Station(clock=clock_factory())
        self.st.open_manifest("M1", "A1")
        self.st.catalog_sample("M1", "S1", "100m", "2026-09-20")
        self.st.appraise("S1", "张工", "石英", "粒状", "正交偏光", 10.0)
        self.assertEqual(self.st.sample("S1").status, Status.RELEASED)

    def test_correction_cancels_release_and_rejudges_by_current_value(self):
        before = len(self.st.ledger())
        e = self.st.correct_sample("S1", "李工", porosity_pct=30.0)
        self.assertEqual(e.kind, "sample_corrected")
        s = self.st.sample("S1")
        self.assertEqual(s.status, Status.REVIEW)          # 旧放行取消，现值越线
        self.assertEqual(s.appraisal.version, 2)           # 新稿
        self.assertEqual(s.appraisal.observer, "李工")     # 更正者即新稿鉴定者
        self.assertIn("旧放行已取消", s.appraisal.reason)
        # 复查者必须不同于新稿鉴定者（而非历史鉴定者）
        with self.assertRaises(DecisionError):
            self.st.review("S1", "李工", passed=True)
        self.st.review("S1", "张工", passed=True)
        self.assertEqual(self.st.sample("S1").status, Status.RELEASED)
        self.assertEqual(len(self.st.ledger()), before + 2)

    def test_correction_back_to_pass_releases_again(self):
        self.st.correct_sample("S1", "李工", porosity_pct=30.0)
        self.st.review("S1", "王工", passed=False)
        self.assertEqual(self.st.sample("S1").status, Status.REJECTED)
        # 重新更正为合格现值：驳回取消，直接按现值放行
        self.st.correct_sample("S1", "赵工", porosity_pct=5.0, polarized="正交偏光")
        s = self.st.sample("S1")
        self.assertEqual(s.status, Status.RELEASED)
        self.assertEqual(s.appraisal.version, 3)

    def test_history_drafts_are_read_only(self):
        self.st.correct_sample("S1", "李工", porosity_pct=30.0)
        rows = self.st.history("S1")
        self.assertEqual([r["kind"] for r in rows], ["sample_cataloged", "appraised", "sample_corrected"])
        # 初判稿仍是 v1 的放行结论，没有被覆盖
        first = rows[1]
        self.assertEqual(first["payload"]["version"], 1)
        self.assertEqual(first["payload"]["decision"], "released")
        # 外部篡改返回的履历不影响台站内部
        rows[1]["payload"]["porosity_pct"] = 999
        self.assertEqual(self.st.history("S1")[1]["payload"]["porosity_pct"], 10.0)

    def test_catalog_only_correction_on_unappraised_sample(self):
        self.st.catalog_sample("M1", "S2", "100m", "2026-09-20")
        self.st.correct_sample("S2", "张工", depth="105m")
        self.assertEqual(self.st.sample("S2").depth, "105m")
        self.assertIsNone(self.st.sample("S2").appraisal)
        with self.assertRaises(DecisionError):
            self.st.correct_sample("S2", "张工", mineral="石英")
        # 放行样本只改编目事实：旧放行也取消并留重判稿
        self.st.correct_sample("S1", "李工", depth="101m")
        s = self.st.sample("S1")
        self.assertEqual(s.status, Status.RELEASED)
        self.assertEqual(s.appraisal.version, 2)
        self.assertIn("旧放行已取消", s.appraisal.reason)

    def test_blank_correction_value_stops(self):
        with self.assertRaises(DecisionError):
            self.st.correct_sample("S1", "李工", depth="  ")
        self.assertEqual(self.st.sample("S1").depth, "100m")


class ViewTests(unittest.TestCase):
    def test_queue_and_overview(self):
        st = Station(clock=clock_factory())
        st.open_manifest("M1", "A1")
        st.catalog_sample("M1", "S1", "100m", "2026-09-20")
        q = st.queue()
        self.assertEqual([v["code"] for v in q["pending_appraisal"]], ["S1"])
        st.appraise("S1", "张工", "石英", "粒状", "", 25.0)
        q = st.queue()
        self.assertEqual(q["pending_appraisal"], [])
        self.assertEqual(q["pending_review"][0]["code"], "S1")
        self.assertEqual(q["pending_review"][0]["first_observer"], "张工")
        o = st.overview()
        self.assertEqual(o["samples_total"], 1)
        self.assertEqual(o["by_status"]["review"], 1)


if __name__ == "__main__":
    unittest.main()
