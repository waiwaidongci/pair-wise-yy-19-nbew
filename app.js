/* UI 接线层：不含业务规则，只调用 Intake / Judgment / Store 三个单元 */
const store = Store.browserStore();
let state = store.load();
let selectedDocId = null;

const banner = document.querySelector("#banner");
const overviewChips = document.querySelector("#overviewChips");
const catalogForm = document.querySelector("#catalogForm");
const identForm = document.querySelector("#identForm");
const identDocSelect = document.querySelector("#identDoc");
const identFormTitle = document.querySelector("#identFormTitle");
const identSubmit = document.querySelector("#identSubmit");
const docList = document.querySelector("#docList");
const reviewQueuePane = document.querySelector("#reviewQueue");
const historyPane = document.querySelector("#historyPane");

const STATUS_LABEL = { inflight: "在途·待鉴定", pending_review: "待复查", released: "已放行" };
const VERDICT_LABEL = { released: "放行", pending_review: "只进复查", cancelled: "已取消·历史稿" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}

function now() {
  return new Date().toISOString();
}

function showBanner(message, isError) {
  banner.textContent = message;
  banner.classList.toggle("error", Boolean(isError));
  banner.hidden = false;
}

function persist() {
  store.save(state);
}

function applyResult(result, okMessage) {
  if (!result.ok) {
    showBanner(result.error, true);
    return false;
  }
  persist();
  render();
  showBanner(okMessage, false);
  return true;
}

/* ---------- 渲染 ---------- */

function renderOverview() {
  const { counts } = Store.overview(state);
  overviewChips.innerHTML = [
    ["在途·待鉴定", counts.inflight],
    ["待复查", counts.pending_review],
    ["已放行", counts.released],
    ["单据总数", counts.total]
  ].map(([label, n]) => `<span class="chip">${label} <b>${n}</b></span>`).join("");
}

function renderDocList() {
  const { docs } = Store.overview(state);
  docList.innerHTML = docs.length ? docs.map((doc) => {
    const history = Store.historyOf(state, doc.id);
    const current = history.versions.length ? history.versions[history.versions.length - 1] : null;
    const actions = [
      doc.status === "inflight" ? `<button type="button" data-identify="${doc.id}">录入鉴定</button>` : "",
      history.versions.length ? `<button type="button" data-correct="${doc.id}">更正</button>` : "",
      `<button type="button" class="ghost" data-history="${doc.id}">履历</button>`
    ].join("");
    return `
      <article class="doc-card status-${doc.status}">
        <header>
          <strong>${esc(doc.id)}</strong>
          <span class="badge">${STATUS_LABEL[doc.status] || doc.status}</span>
        </header>
        <p>箱号 ${esc(doc.boxNo)} · 样本 ${esc(doc.sampleNo)} · 深度 ${esc(doc.depth)} m · ${esc(doc.date)}</p>
        ${current ? `<p>现稿 V${current.version}：${esc(current.minerals)}｜${esc(current.texture)}｜偏光 ${esc(current.polarization) || "缺失"}｜孔隙 ${current.porosity}%</p>` : "<p>尚未录入鉴定。</p>"}
        <div class="card-actions">${actions}</div>
      </article>`;
  }).join("") : "<p class=\"empty\">暂无单据，请先在左侧编目落单。</p>";
}

function renderReviewQueue() {
  const queue = Store.reviewQueue(state);
  reviewQueuePane.innerHTML = queue.length ? queue.map((item) => `
    <article class="queue-item">
      <p><strong>${esc(item.docId)}</strong> · 箱号 ${esc(item.boxNo)} · 样本 ${esc(item.sampleNo)}</p>
      <p>首次鉴定者 ${esc(item.identifier)}｜孔隙 ${item.porosity}%｜偏光 ${esc(item.polarization) || "缺失"}</p>
      <div class="review-row">
        <input type="text" placeholder="复查者（须不同于首次鉴定者）" data-reviewer-for="${item.docId}">
        <button type="button" data-approve="${item.docId}">复查放行</button>
      </div>
    </article>`).join("") : "<p class=\"empty\">复查队列为空。</p>";
}

function renderHistory() {
  if (!selectedDocId) {
    historyPane.innerHTML = "<p class=\"empty\">在总览中点「履历」查看单据鉴定稿历史。</p>";
    return;
  }
  const history = Store.historyOf(state, selectedDocId);
  if (!history) {
    historyPane.innerHTML = "<p class=\"empty\">单据不存在。</p>";
    return;
  }
  const versions = history.versions.length ? history.versions.map((v) => `
    <li class="version ${v.readonly ? "readonly" : "current"}">
      <header>
        <strong>V${v.version}</strong>
        <span class="badge">${VERDICT_LABEL[v.verdict] || v.verdict}</span>
        ${v.readonly ? "<span class=\"tag\">只读</span>" : "<span class=\"tag current-tag\">现稿</span>"}
      </header>
      <p>矿物组成：${esc(v.minerals)}</p>
      <p>结构：${esc(v.texture)}</p>
      <p>偏光：${esc(v.polarization) || "缺失"}｜孔隙占比：${v.porosity}%</p>
      <p>鉴定者：${esc(v.identifier)}${v.reviewer ? `｜复查者：${esc(v.reviewer)}` : ""}</p>
      <p class="muted">${esc(v.createdAt)}</p>
    </li>`).join("") : "<p class=\"empty\">尚无鉴定稿。</p>";
  historyPane.innerHTML = `
    <h3>${esc(history.docId)} · 箱号 ${esc(history.boxNo)} · 样本 ${esc(history.sampleNo)}</h3>
    <p>当前状态：${STATUS_LABEL[history.status] || history.status}</p>
    <ol class="timeline">${versions}</ol>`;
}

function renderIdentDocOptions() {
  const previous = identDocSelect.value;
  const eligible = state.docs;
  identDocSelect.innerHTML = eligible.length ? eligible.map((doc) => {
    const mode = doc.status === "inflight" ? "首次鉴定" : "更正重判";
    return `<option value="${doc.id}">${doc.id} · ${doc.boxNo} · ${doc.sampleNo}（${mode}）</option>`;
  }).join("") : "<option value=\"\">暂无单据</option>";
  if (previous && eligible.some((doc) => doc.id === previous)) {
    identDocSelect.value = previous;
  }
  syncIdentFormMode();
}

function render() {
  renderOverview();
  renderDocList();
  renderReviewQueue();
  renderHistory();
  renderIdentDocOptions();
}

/* ---------- 事件 ---------- */

catalogForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(catalogForm);
  const result = Intake.catalog(state, {
    boxNo: data.get("boxNo"),
    sampleNo: data.get("sampleNo"),
    depth: data.get("depth"),
    date: data.get("date")
  }, now());
  if (applyResult(result, `单据 ${result.doc ? result.doc.id : ""} 编目落单成功`)) {
    catalogForm.reset();
  }
});

function fillIdentForm(docId) {
  identDocSelect.value = docId;
  const history = Store.historyOf(state, docId);
  const current = history && history.versions.length ? history.versions[history.versions.length - 1] : null;
  identForm.elements.minerals.value = current ? current.minerals : "";
  identForm.elements.texture.value = current ? current.texture : "";
  identForm.elements.polarization.value = current ? current.polarization : "";
  identForm.elements.porosity.value = current ? current.porosity : "";
  identForm.elements.identifier.value = current ? current.identifier : "";
  syncIdentFormMode();
}

function syncIdentFormMode() {
  const doc = Intake.findDoc(state, identDocSelect.value);
  const isFirst = doc && doc.status === "inflight";
  identFormTitle.textContent = isFirst ? "鉴定录入（首次鉴定）" : "样本更正（旧放行取消，按现值重判）";
  identSubmit.textContent = isFirst ? "提交鉴定" : "提交更正";
}

identDocSelect.addEventListener("change", () => fillIdentForm(identDocSelect.value));

identForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const docId = identDocSelect.value;
  const doc = Intake.findDoc(state, docId);
  if (!doc) {
    showBanner("请先选择单据", true);
    return;
  }
  const data = new FormData(identForm);
  const fields = {
    minerals: data.get("minerals"),
    texture: data.get("texture"),
    polarization: data.get("polarization"),
    porosity: data.get("porosity"),
    identifier: data.get("identifier")
  };
  const isFirst = doc.status === "inflight";
  const result = isFirst
    ? Intake.identify(state, docId, fields, now())
    : Intake.correct(state, docId, fields, now());
  const version = result.version;
  const verdictText = version ? `，判定：${VERDICT_LABEL[version.verdict]}` : "";
  applyResult(result, `${isFirst ? "鉴定" : "更正"}已写入 ${docId} V${version ? version.version : ""}${verdictText}`);
});

docList.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  const { identify, correct, history } = target.dataset;
  if (identify) fillIdentForm(identify);
  if (correct) fillIdentForm(correct);
  if (history) {
    selectedDocId = history;
    renderHistory();
  }
});

reviewQueuePane.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-approve]");
  if (!button) return;
  const docId = button.dataset.approve;
  const input = reviewQueuePane.querySelector(`input[data-reviewer-for="${docId}"]`);
  const result = Intake.submitReview(state, docId, input ? input.value : "", now());
  applyResult(result, `单据 ${docId} 复查通过，已放行`);
});

render();
