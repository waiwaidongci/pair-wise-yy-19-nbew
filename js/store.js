/*
 * 持久化单元（store）
 * 负责状态的落盘与重载，并从持久化数据派生三个只读视图：
 * 总览（overview）、复查队列（reviewQueue）、履历（historyOf）。
 * 视图只由同一份落盘数据重建，保证重载后三者吻合。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.Store = api;
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  const STORAGE_KEY = "core-thin-section-release-station/v1";

  function emptyState() {
    return { seq: 0, docs: [] };
  }

  // 重载时规整数据形状，坏档/旧档不拖垮台面
  function sanitize(raw) {
    const state = emptyState();
    if (!raw || typeof raw !== "object") return state;
    state.seq = Number.isInteger(raw.seq) && raw.seq >= 0 ? raw.seq : 0;
    if (Array.isArray(raw.docs)) {
      state.docs = raw.docs
        .filter((doc) => doc && typeof doc === "object" && doc.id)
        .map((doc) => ({
          id: String(doc.id),
          boxNo: String(doc.boxNo || ""),
          sampleNo: String(doc.sampleNo || ""),
          depth: String(doc.depth || ""),
          date: String(doc.date || ""),
          status: String(doc.status || "inflight"),
          versions: Array.isArray(doc.versions) ? doc.versions : [],
          createdAt: String(doc.createdAt || ""),
          updatedAt: String(doc.updatedAt || "")
        }));
    }
    return state;
  }

  // backend 形如 localStorage：{ getItem, setItem, removeItem }
  function createStore(backend) {
    return {
      load() {
        try {
          const raw = backend.getItem(STORAGE_KEY);
          return sanitize(raw ? JSON.parse(raw) : null);
        } catch (err) {
          return emptyState();
        }
      },
      save(state) {
        backend.setItem(STORAGE_KEY, JSON.stringify(state));
      },
      reset() {
        backend.removeItem(STORAGE_KEY);
      }
    };
  }

  function browserStore() {
    return createStore(window.localStorage);
  }

  function currentVersion(doc) {
    return doc.versions.length ? doc.versions[doc.versions.length - 1] : null;
  }

  // 总览：按状态统计 + 单据清单
  function overview(state) {
    const counts = { inflight: 0, pending_review: 0, released: 0, total: state.docs.length };
    for (const doc of state.docs) {
      if (doc.status in counts) counts[doc.status] += 1;
    }
    return {
      counts,
      docs: state.docs.map((doc) => ({
        id: doc.id,
        boxNo: doc.boxNo,
        sampleNo: doc.sampleNo,
        depth: doc.depth,
        date: doc.date,
        status: doc.status,
        versionCount: doc.versions.length
      }))
    };
  }

  // 复查队列：只进复查的单据及其当前稿要点
  function reviewQueue(state) {
    return state.docs
      .filter((doc) => doc.status === "pending_review")
      .map((doc) => {
        const version = currentVersion(doc);
        return {
          docId: doc.id,
          boxNo: doc.boxNo,
          sampleNo: doc.sampleNo,
          identifier: version ? version.identifier : "",
          porosity: version ? version.porosity : null,
          polarization: version ? version.polarization : ""
        };
      });
  }

  // 履历：单据全部鉴定稿，历史稿只读
  function historyOf(state, docId) {
    const doc = state.docs.find((item) => item.id === docId);
    if (!doc) return null;
    return {
      docId: doc.id,
      boxNo: doc.boxNo,
      sampleNo: doc.sampleNo,
      status: doc.status,
      versions: doc.versions.map((version) => ({
        version: version.version,
        minerals: version.minerals,
        texture: version.texture,
        polarization: version.polarization,
        porosity: version.porosity,
        identifier: version.identifier,
        reviewer: version.reviewer,
        verdict: version.verdict,
        readonly: Boolean(version.readonly),
        createdAt: version.createdAt
      }))
    };
  }

  return Object.freeze({
    STORAGE_KEY,
    emptyState,
    createStore,
    browserStore,
    overview,
    reviewQueue,
    historyOf
  });
});
