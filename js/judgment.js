/*
 * 判定单元（judgment）
 * 纯业务规则，不读写状态、不接触持久化：
 *  - 孔隙占比越过 20% 或偏光缺失 → 只进复查
 *  - 复查者必须与首次鉴定者不同
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.Judgment = api;
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  const POROSITY_REVIEW_LIMIT = 20; // 孔隙占比上限（%），越过即进复查

  const VERDICT = Object.freeze({
    RELEASED: "released",           // 放行
    PENDING_REVIEW: "pending_review", // 只进复查
    CANCELLED: "cancelled"          // 已被更正取消的旧放行
  });

  function isBlank(value) {
    return value === undefined || value === null || String(value).trim() === "";
  }

  // 按当前鉴定值判定去向：放行 或 只进复查
  function judge(ident) {
    if (isBlank(ident.polarization)) return VERDICT.PENDING_REVIEW; // 偏光缺失
    if (Number(ident.porosity) > POROSITY_REVIEW_LIMIT) return VERDICT.PENDING_REVIEW; // 孔隙越过 20%
    return VERDICT.RELEASED;
  }

  // 复查者须与首次鉴定者不同
  function reviewerAllowed(identifier, reviewer) {
    if (isBlank(reviewer)) return false;
    if (isBlank(identifier)) return true;
    return String(identifier).trim() !== String(reviewer).trim();
  }

  // 生成可读的判定依据，供界面与履历展示
  function verdictReasons(ident) {
    const reasons = [];
    if (isBlank(ident.polarization)) reasons.push("偏光缺失");
    const porosity = Number(ident.porosity);
    if (Number.isFinite(porosity) && porosity > POROSITY_REVIEW_LIMIT) {
      reasons.push(`孔隙占比 ${porosity}% 越过 ${POROSITY_REVIEW_LIMIT}%`);
    }
    return reasons;
  }

  return Object.freeze({ POROSITY_REVIEW_LIMIT, VERDICT, isBlank, judge, reviewerAllowed, verdictReasons });
});
