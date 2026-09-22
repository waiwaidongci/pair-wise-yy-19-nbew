/*
 * 入口单元（intake）
 * 所有业务写入的唯一入口：编目、首次鉴定、复查放行、样本更正。
 * 约定：先完整校验、后改动状态 —— 任一规则不通过即整体拒绝，不产生半成品。
 * 本单元不负责持久化，只改动内存状态；落盘由 store 单元完成。
 */
(function (root, factory) {
  const isNode = typeof module !== "undefined" && module.exports;
  const Judgment = isNode ? require("./judgment") : root.Judgment;
  const api = factory(Judgment);
  if (isNode) module.exports = api;
  if (root) root.Intake = api;
})(typeof self !== "undefined" ? self : globalThis, function (Judgment) {
  "use strict";

  const DOC_STATUS = Object.freeze({
    INFLIGHT: "inflight",            // 在途：已编目、待鉴定
    PENDING_REVIEW: "pending_review", // 在途：鉴定只进复查
    RELEASED: "released"             // 已放行
  });

  // 占用收纳箱的“在途”状态；已放行单据不再占用箱号
  const OPEN_STATUSES = Object.freeze([DOC_STATUS.INFLIGHT, DOC_STATUS.PENDING_REVIEW]);

  function fail(message) {
    return { ok: false, error: message };
  }

  function ok(extra) {
    return Object.assign({ ok: true }, extra);
  }

  function findDoc(state, docId) {
    return state.docs.find((doc) => doc.id === docId) || null;
  }

  function currentVersion(doc) {
    return doc.versions.length ? doc.versions[doc.versions.length - 1] : null;
  }

  function nextDocId(state) {
    state.seq += 1;
    return `D-${String(state.seq).padStart(4, "0")}`;
  }

  // 编目：编号、深度或日期空白即停补录；每个收纳箱最多一张在途单据
  function catalog(state, fields, now) {
    const boxNo = String(fields.boxNo || "").trim();
    const sampleNo = String(fields.sampleNo || "").trim();
    const depth = String(fields.depth || "").trim();
    const date = String(fields.date || "").trim();

    if (Judgment.isBlank(sampleNo)) return fail("样本编号空白，停补录");
    if (Judgment.isBlank(depth)) return fail("深度空白，停补录");
    if (Judgment.isBlank(date)) return fail("日期空白，停补录");
    if (Judgment.isBlank(boxNo)) return fail("收纳箱号空白，停补录");

    const clash = state.docs.find((doc) => doc.boxNo === boxNo && OPEN_STATUSES.includes(doc.status));
    if (clash) return fail(`收纳箱 ${boxNo} 已有在途单据 ${clash.id}，本次编目未落单`);

    const doc = {
      id: nextDocId(state),
      boxNo,
      sampleNo,
      depth,
      date,
      status: DOC_STATUS.INFLIGHT,
      versions: [],
      createdAt: now,
      updatedAt: now
    };
    state.docs.push(doc);
    return ok({ doc });
  }

  // 鉴定四要素：矿物组成、结构、偏光、孔隙占比；偏光允许缺失（缺失则只进复查）
  function validateIdentification(fields) {
    const minerals = String(fields.minerals || "").trim();
    const texture = String(fields.texture || "").trim();
    const polarization = String(fields.polarization || "").trim();
    const identifier = String(fields.identifier || "").trim();
    const porosity = Number(String(fields.porosity ?? "").trim());

    if (Judgment.isBlank(minerals)) return fail("矿物组成空白，停补录");
    if (Judgment.isBlank(texture)) return fail("结构空白，停补录");
    if (Judgment.isBlank(identifier)) return fail("鉴定者空白，停补录");
    if (Judgment.isBlank(fields.porosity) || !Number.isFinite(porosity) || porosity < 0 || porosity > 100) {
      return fail("孔隙占比须为 0–100 的数字，停补录");
    }
    return ok({ ident: { minerals, texture, polarization, porosity, identifier } });
  }

  function appendVersion(state, doc, ident, now) {
    const version = {
      version: doc.versions.length + 1,
      minerals: ident.minerals,
      texture: ident.texture,
      polarization: ident.polarization,
      porosity: ident.porosity,
      identifier: ident.identifier,
      reviewer: "",
      verdict: Judgment.judge(ident),
      readonly: false,
      createdAt: now
    };
    doc.versions.push(version);
    doc.status = version.verdict === Judgment.VERDICT.RELEASED
      ? DOC_STATUS.RELEASED
      : DOC_STATUS.PENDING_REVIEW;
    doc.updatedAt = now;
    return version;
  }

  // 首次鉴定：仅对在途（待鉴定）单据开放
  function identify(state, docId, fields, now) {
    const doc = findDoc(state, docId);
    if (!doc) return fail(`单据 ${docId} 不存在`);
    if (doc.status !== DOC_STATUS.INFLIGHT) return fail(`单据 ${doc.id} 不在待鉴定状态，请走更正入口`);

    const check = validateIdentification(fields);
    if (!check.ok) return check;

    const version = appendVersion(state, doc, check.ident, now);
    return ok({ doc, version });
  }

  // 复查放行：复查者须与首次鉴定者不同
  function submitReview(state, docId, reviewer, now) {
    const doc = findDoc(state, docId);
    if (!doc) return fail(`单据 ${docId} 不存在`);
    if (doc.status !== DOC_STATUS.PENDING_REVIEW) return fail(`单据 ${doc.id} 不在复查队列`);

    const version = currentVersion(doc);
    if (!Judgment.reviewerAllowed(version.identifier, reviewer)) {
      return fail("复查者须与首次鉴定者不同");
    }
    version.reviewer = String(reviewer).trim();
    version.verdict = Judgment.VERDICT.RELEASED;
    doc.status = DOC_STATUS.RELEASED;
    doc.updatedAt = now;
    return ok({ doc, version });
  }

  // 样本更正：旧放行取消，历史稿只读，按现值重判
  function correct(state, docId, fields, now) {
    const doc = findDoc(state, docId);
    if (!doc) return fail(`单据 ${docId} 不存在`);
    if (!doc.versions.length) return fail(`单据 ${doc.id} 尚无鉴定稿，请走首次鉴定`);

    const check = validateIdentification(fields);
    if (!check.ok) return check;

    const previous = currentVersion(doc);
    previous.verdict = Judgment.VERDICT.CANCELLED; // 旧放行取消
    previous.readonly = true;                      // 历史稿只读

    const version = appendVersion(state, doc, check.ident, now); // 按现值重判
    return ok({ doc, version, cancelled: previous });
  }

  return Object.freeze({
    DOC_STATUS,
    OPEN_STATUSES,
    catalog,
    identify,
    submitReview,
    correct,
    currentVersion,
    findDoc
  });
});
