/* 业务规则自测：node tests/run-tests.js */
const assert = require("assert");
const Judgment = require("../js/judgment");
const Intake = require("../js/intake");
const Store = require("../js/store");

function memoryBackend() {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key)
  };
}

const T = "2026-09-22T08:00:00.000Z";
const identFields = (over = {}) => ({
  minerals: "石英、斜长石",
  texture: "半自形粒状结构",
  polarization: "正交偏光",
  porosity: "12.5",
  identifier: "张工",
  ...over
});

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok ${passed} - ${name}`);
}

function freshState() {
  return Store.emptyState();
}

function catalogOk(state, boxNo, over = {}) {
  const result = Intake.catalog(state, {
    boxNo, sampleNo: "YP-001", depth: "125.4", date: "2026-09-22", ...over
  }, T);
  assert.strictEqual(result.ok, true, result.error);
  return result.doc;
}

// 1. 编号、深度或日期空白即停补录
test("编号/深度/日期空白即停补录，且不产生单据", () => {
  const state = freshState();
  for (const blank of ["sampleNo", "depth", "date"]) {
    const fields = { boxNo: "BX-01", sampleNo: "YP-001", depth: "125.4", date: "2026-09-22", [blank]: "  " };
    const result = Intake.catalog(state, fields, T);
    assert.strictEqual(result.ok, false);
    assert.match(result.error, /停补录/);
  }
  assert.strictEqual(state.docs.length, 0);
});

// 2. 每个收纳箱最多一张在途单据；冲突不产生半成品
test("箱号冲突拒绝落单，状态保持原样（无半成品）", () => {
  const state = freshState();
  const first = catalogOk(state, "BX-01");
  const snapshot = JSON.stringify(state);
  const conflict = Intake.catalog(state, { boxNo: "BX-01", sampleNo: "YP-002", depth: "130", date: "2026-09-22" }, T);
  assert.strictEqual(conflict.ok, false);
  assert.match(conflict.error, /在途单据/);
  assert.strictEqual(state.docs.length, 1);
  assert.strictEqual(JSON.stringify(state), snapshot); // 原单据未被污染
  assert.strictEqual(state.docs[0].id, first.id);
});

// 3. 箱号在放行后释放，可再编目
test("在途单据放行后，同一箱号可再次编目", () => {
  const state = freshState();
  const doc = catalogOk(state, "BX-01");
  Intake.identify(state, doc.id, identFields(), T);
  assert.strictEqual(doc.status, "released");
  const again = Intake.catalog(state, { boxNo: "BX-01", sampleNo: "YP-002", depth: "131", date: "2026-09-22" }, T);
  assert.strictEqual(again.ok, true);
  assert.strictEqual(state.docs.length, 2);
});

// 4. 鉴定写入矿物组成、结构、偏光与孔隙占比
test("首次鉴定写入四要素", () => {
  const state = freshState();
  const doc = catalogOk(state, "BX-02");
  const result = Intake.identify(state, doc.id, identFields(), T);
  assert.strictEqual(result.ok, true);
  const v = result.version;
  assert.strictEqual(v.minerals, "石英、斜长石");
  assert.strictEqual(v.texture, "半自形粒状结构");
  assert.strictEqual(v.polarization, "正交偏光");
  assert.strictEqual(v.porosity, 12.5);
  assert.strictEqual(v.identifier, "张工");
});

// 5. 孔隙越过 20% 只进复查；等于 20% 放行；偏光缺失只进复查
test("孔隙>20% 或偏光缺失只进复查，否则放行", () => {
  const state = freshState();
  const d1 = catalogOk(state, "BX-10");
  Intake.identify(state, d1.id, identFields({ porosity: "20.1" }), T);
  assert.strictEqual(d1.status, "pending_review");

  const d2 = catalogOk(state, "BX-11");
  Intake.identify(state, d2.id, identFields({ porosity: "20" }), T); // 恰好 20% 不算越过
  assert.strictEqual(d2.status, "released");

  const d3 = catalogOk(state, "BX-12");
  Intake.identify(state, d3.id, identFields({ polarization: "", porosity: "5" }), T);
  assert.strictEqual(d3.status, "pending_review");

  const d4 = catalogOk(state, "BX-13");
  Intake.identify(state, d4.id, identFields({ porosity: "8" }), T);
  assert.strictEqual(d4.status, "released");
});

// 6. 复查者与首次鉴定者必须不同
test("复查者相同被拒，不同则放行", () => {
  const state = freshState();
  const doc = catalogOk(state, "BX-20");
  Intake.identify(state, doc.id, identFields({ porosity: "35", identifier: "张工" }), T);
  assert.strictEqual(doc.status, "pending_review");

  const same = Intake.submitReview(state, doc.id, "张工", T);
  assert.strictEqual(same.ok, false);
  assert.match(same.error, /不同/);
  assert.strictEqual(doc.status, "pending_review");

  const other = Intake.submitReview(state, doc.id, "李工", T);
  assert.strictEqual(other.ok, true);
  assert.strictEqual(doc.status, "released");
  assert.strictEqual(Intake.currentVersion(doc).reviewer, "李工");
});

// 7. 样本更正：旧放行取消、历史稿只读、按现值重判
test("更正取消旧放行，历史稿只读且内容不变，按现值重判", () => {
  const state = freshState();
  const doc = catalogOk(state, "BX-30");
  Intake.identify(state, doc.id, identFields({ porosity: "10" }), T);
  assert.strictEqual(doc.status, "released");
  const oldSnapshot = JSON.parse(JSON.stringify(doc.versions[0]));

  const corrected = Intake.correct(state, doc.id, identFields({ porosity: "28", minerals: "石英、方解石" }), T);
  assert.strictEqual(corrected.ok, true);
  assert.strictEqual(doc.status, "pending_review"); // 按现值 28% 重判 → 复查
  assert.strictEqual(doc.versions.length, 2);

  const old = doc.versions[0];
  assert.strictEqual(old.verdict, "cancelled"); // 旧放行取消
  assert.strictEqual(old.readonly, true);       // 历史稿只读
  for (const key of ["minerals", "texture", "polarization", "porosity", "identifier"]) {
    assert.deepStrictEqual(old[key], oldSnapshot[key], `历史稿字段 ${key} 被改动`);
  }
  const current = Intake.currentVersion(doc);
  assert.strictEqual(current.version, 2);
  assert.strictEqual(current.porosity, 28);
  assert.strictEqual(current.verdict, "pending_review");
});

// 8. 更正后复查通过，箱号随之释放
test("更正稿复查放行后箱号释放", () => {
  const state = freshState();
  const doc = catalogOk(state, "BX-31");
  Intake.identify(state, doc.id, identFields({ porosity: "10" }), T);
  Intake.correct(state, doc.id, identFields({ porosity: "28" }), T);
  Intake.submitReview(state, doc.id, "王工", T);
  assert.strictEqual(doc.status, "released");
  const again = Intake.catalog(state, { boxNo: "BX-31", sampleNo: "YP-009", depth: "99", date: "2026-09-22" }, T);
  assert.strictEqual(again.ok, true);
});

// 9. 重载后总览、队列与履历吻合
test("落盘重载后，总览/队列/履历与内存态一致", () => {
  const backend = memoryBackend();
  const storeA = Store.createStore(backend);
  const state = storeA.load();

  const d1 = catalogOk(state, "BX-40");
  Intake.identify(state, d1.id, identFields({ porosity: "8" }), T);          // 放行
  const d2 = catalogOk(state, "BX-41");
  Intake.identify(state, d2.id, identFields({ porosity: "33" }), T);         // 复查
  const d3 = catalogOk(state, "BX-42");                                      // 在途待鉴定
  Intake.correct(state, d2.id, identFields({ porosity: "31", identifier: "赵工" }), T); // 更正仍复查
  storeA.save(state);

  // 模拟重载：同一后端、全新 Store 实例
  const reloaded = Store.createStore(backend).load();
  assert.deepStrictEqual(Store.overview(reloaded), Store.overview(state));
  assert.deepStrictEqual(Store.reviewQueue(reloaded), Store.reviewQueue(state));
  for (const doc of state.docs) {
    assert.deepStrictEqual(Store.historyOf(reloaded, doc.id), Store.historyOf(state, doc.id));
  }

  const { counts } = Store.overview(reloaded);
  assert.deepStrictEqual(counts, { inflight: 1, pending_review: 1, released: 1, total: 3 });
  assert.strictEqual(Store.reviewQueue(reloaded).length, 1);
  assert.strictEqual(Store.reviewQueue(reloaded)[0].docId, d2.id);
  assert.strictEqual(Store.historyOf(reloaded, d2.id).versions.length, 2);
  assert.strictEqual(Store.historyOf(reloaded, d3.id).versions.length, 0);
});

// 10. 坏档重载不拖垮台面
test("坏档重载回退为空台", () => {
  const backend = memoryBackend();
  backend.setItem(Store.STORAGE_KEY, "{not-json");
  const state = Store.createStore(backend).load();
  assert.deepStrictEqual(state, { seq: 0, docs: [] });
});

console.log(`\n${passed} 项规则自测全部通过`);
