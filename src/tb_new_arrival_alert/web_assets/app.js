const state = {
  config: null,
  selectedTarget: 0,
  activeTab: "radar",
  radarItems: [],
  radarSamples: [],
  radarReviews: [],
  lastStatePayload: null,
  lastRadarPayload: null,
  editingRadarItemId: null,
  editingRadarSampleId: null,
};

const el = (id) => document.getElementById(id);

const fields = {
  appOverview: el("appOverview"),
  configPath: el("configPath"),
  targetList: el("targetList"),
  targetForm: el("targetForm"),
  globalForm: el("globalForm"),
  targetName: el("targetName"),
  targetUrl: el("targetUrl"),
  targetEnabled: el("targetEnabled"),
  includeKeywords: el("includeKeywords"),
  excludeKeywords: el("excludeKeywords"),
  priceMin: el("priceMin"),
  priceMax: el("priceMax"),
  pollInterval: el("pollInterval"),
  fetcherType: el("fetcherType"),
  notifyOnFirstScan: el("notifyOnFirstScan"),
  userAgent: el("userAgent"),
  timeoutSeconds: el("timeoutSeconds"),
  waitSeconds: el("waitSeconds"),
  userDataDir: el("userDataDir"),
  webhookEnabled: el("webhookEnabled"),
  webhookUrl: el("webhookUrl"),
  radarView: el("radarView"),
  radarDbPath: el("radarDbPath"),
  radarOverview: el("radarOverview"),
  radarList: el("radarList"),
  aggregateCount: el("aggregateCount"),
  aggregateList: el("aggregateList"),
  releaseWatchCount: el("releaseWatchCount"),
  releaseWatchList: el("releaseWatchList"),
  collectionTaskCount: el("collectionTaskCount"),
  collectionTaskList: el("collectionTaskList"),
  watchRecommendationCount: el("watchRecommendationCount"),
  watchRecommendationList: el("watchRecommendationList"),
  reviewCount: el("reviewCount"),
  reviewStats: el("reviewStats"),
  reviewList: el("reviewList"),
  radarItemForm: el("radarItemForm"),
  radarEditingItemId: el("radarEditingItemId"),
  radarEditItemId: el("radarEditItemId"),
  loadRadarItemBtn: el("loadRadarItemBtn"),
  newRadarItemBtn: el("newRadarItemBtn"),
  deleteRadarItemBtn: el("deleteRadarItemBtn"),
  addRadarItemBtn: el("addRadarItemBtn"),
  radarBrandName: el("radarBrandName"),
  radarSeriesName: el("radarSeriesName"),
  radarItemName: el("radarItemName"),
  radarCategory: el("radarCategory"),
  radarColorway: el("radarColorway"),
  radarOriginalPriceJpy: el("radarOriginalPriceJpy"),
  radarJpyToCny: el("radarJpyToCny"),
  radarReleaseSignalScore: el("radarReleaseSignalScore"),
  radarReleaseDate: el("radarReleaseDate"),
  radarJapanShipping: el("radarJapanShipping"),
  radarInternationalShipping: el("radarInternationalShipping"),
  radarProxyFee: el("radarProxyFee"),
  radarTaxBuffer: el("radarTaxBuffer"),
  radarItemSourceUrl: el("radarItemSourceUrl"),
  radarSampleForm: el("radarSampleForm"),
  radarEditingSampleId: el("radarEditingSampleId"),
  newRadarSampleBtn: el("newRadarSampleBtn"),
  addRadarSampleBtn: el("addRadarSampleBtn"),
  radarSampleItemId: el("radarSampleItemId"),
  radarSampleSourceType: el("radarSampleSourceType"),
  radarListingStatus: el("radarListingStatus"),
  radarListedPriceCny: el("radarListedPriceCny"),
  radarSoldPriceCny: el("radarSoldPriceCny"),
  radarCondition: el("radarCondition"),
  radarConfidence: el("radarConfidence"),
  radarSampleTitle: el("radarSampleTitle"),
  radarSampleSourceUrl: el("radarSampleSourceUrl"),
  radarImportForm: el("radarImportForm"),
  radarImportPath: el("radarImportPath"),
  radarWatchForm: el("radarWatchForm"),
  radarWatchItemId: el("radarWatchItemId"),
  radarWatchUrl: el("radarWatchUrl"),
  radarWatchPriceMax: el("radarWatchPriceMax"),
  radarReviewForm: el("radarReviewForm"),
  radarReviewItemId: el("radarReviewItemId"),
  radarReviewStatus: el("radarReviewStatus"),
  radarObservedPriceCny: el("radarObservedPriceCny"),
  radarReviewWindowDays: el("radarReviewWindowDays"),
  radarReviewNotes: el("radarReviewNotes"),
  radarCollectForm: el("radarCollectForm"),
  radarCollectItemId: el("radarCollectItemId"),
  radarCollectSourceType: el("radarCollectSourceType"),
  radarCollectStatus: el("radarCollectStatus"),
  radarCollectConfidence: el("radarCollectConfidence"),
  radarCollectText: el("radarCollectText"),
  radarReleaseCollectForm: el("radarReleaseCollectForm"),
  radarReleaseCollectBrand: el("radarReleaseCollectBrand"),
  radarReleaseCollectSeries: el("radarReleaseCollectSeries"),
  radarReleaseCollectCategory: el("radarReleaseCollectCategory"),
  radarReleaseCollectDate: el("radarReleaseCollectDate"),
  radarReleaseCollectJpyToCny: el("radarReleaseCollectJpyToCny"),
  radarReleaseCollectSignal: el("radarReleaseCollectSignal"),
  radarReleaseCollectUrl: el("radarReleaseCollectUrl"),
  radarReleaseCollectText: el("radarReleaseCollectText"),
  sampleLedgerCount: el("sampleLedgerCount"),
  sampleLedgerList: el("sampleLedgerList"),
  scanStatus: el("scanStatus"),
  summaryGrid: el("summaryGrid"),
  resultList: el("resultList"),
  toast: el("toast"),
};

function emptyConfig() {
  return {
    poll_interval_seconds: 90,
    notify_on_first_scan: false,
    data_dir: ".data",
    fetcher: { type: "http", timeout_seconds: 20, user_agent: "" },
    notifications: [{ type: "console", enabled: true }],
    targets: [],
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `请求失败: ${response.status}`);
  }
  return payload;
}

async function loadAll() {
  const [configPayload, statePayload] = await Promise.all([
    api("/api/config"),
    api("/api/state"),
  ]);
  state.config = configPayload.config || emptyConfig();
  state.lastStatePayload = statePayload;
  if (!Array.isArray(state.config.targets)) state.config.targets = [];
  fields.configPath.textContent = statePayload.config_path;
  if (state.selectedTarget >= state.config.targets.length) {
    state.selectedTarget = Math.max(0, state.config.targets.length - 1);
  }
  renderTargets();
  renderForms();
  renderSummaryFromState(statePayload);
  renderAppOverview();
  await loadRadar();
}

function renderTargets() {
  const targets = state.config.targets || [];
  fields.targetList.innerHTML = "";
  if (targets.length === 0) {
    fields.targetList.innerHTML = '<div class="empty">暂无目标</div>';
    return;
  }
  targets.forEach((target, index) => {
    const button = document.createElement("button");
    button.className = `target-row ${index === state.selectedTarget ? "active" : ""}`;
    button.type = "button";
    button.innerHTML = `
      <span class="target-row-head">
        <i class="status-dot ${target.enabled === false ? "off" : "on"}"></i>
        <strong>${escapeHtml(target.name || "未命名目标")}</strong>
      </span>
      <span class="target-url">${target.enabled === false ? "停用" : "启用"} · ${escapeHtml(target.url || "未填写 URL")}</span>
      <span class="target-meta">${target.include_keywords?.length || 0} 包含 · ${target.exclude_keywords?.length || 0} 排除</span>
    `;
    button.addEventListener("click", () => {
      persistCurrentForm();
      state.selectedTarget = index;
      renderTargets();
      renderForms();
    });
    fields.targetList.appendChild(button);
  });
}

function renderForms() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === state.activeTab);
  });
  fields.targetForm.classList.toggle("hidden", state.activeTab !== "target");
  fields.globalForm.classList.toggle("hidden", state.activeTab !== "global");
  fields.radarView.classList.toggle("hidden", state.activeTab !== "radar");

  const target = currentTarget();
  fields.targetName.value = target?.name || "";
  fields.targetUrl.value = target?.url || "";
  fields.targetEnabled.checked = target?.enabled !== false;
  fields.includeKeywords.value = (target?.include_keywords || []).join("\n");
  fields.excludeKeywords.value = (target?.exclude_keywords || []).join("\n");
  fields.priceMin.value = target?.price_min ?? "";
  fields.priceMax.value = target?.price_max ?? "";

  const fetcher = state.config.fetcher || {};
  const webhook = getWebhook();
  fields.pollInterval.value = state.config.poll_interval_seconds ?? 90;
  fields.fetcherType.value = fetcher.type || "http";
  fields.notifyOnFirstScan.checked = Boolean(state.config.notify_on_first_scan);
  fields.userAgent.value = fetcher.user_agent || "";
  fields.timeoutSeconds.value = fetcher.timeout_seconds ?? 20;
  fields.waitSeconds.value = fetcher.wait_seconds ?? 5;
  fields.userDataDir.value = fetcher.user_data_dir || ".browser-profile";
  fields.webhookEnabled.checked = Boolean(webhook?.enabled);
  fields.webhookUrl.value = webhook?.url || "";
}

function renderSummaryFromState(payload) {
  const targets = payload.targets || [];
  const seenCounts = payload.seen_counts || {};
  const enabled = targets.filter((target) => target.enabled !== false).length;
  const seen = Object.values(seenCounts).reduce((sum, count) => sum + Number(count || 0), 0);
  fields.summaryGrid.innerHTML = `
    <div class="metric"><strong>${targets.length}</strong><span>目标数</span></div>
    <div class="metric"><strong>${enabled}</strong><span>启用中</span></div>
    <div class="metric"><strong>${seen}</strong><span>已记录商品</span></div>
  `;
}

async function loadRadar() {
  const payload = await api("/api/radar");
  state.lastRadarPayload = payload;
  fields.radarDbPath.textContent = payload.db_path || "";
  renderRadarPayload(payload);
  renderAppOverview();
}

function renderAppOverview() {
  const statePayload = state.lastStatePayload || {};
  const radarPayload = state.lastRadarPayload || {};
  const targets = statePayload.targets || state.config?.targets || [];
  const seenCounts = statePayload.seen_counts || {};
  const enabled = targets.filter((target) => target.enabled !== false).length;
  const seen = Object.values(seenCounts).reduce((sum, count) => sum + Number(count || 0), 0);
  const results = radarPayload.results || [];
  const recommendations = radarPayload.watch_recommendations || [];
  const tasks = radarPayload.collection_tasks || [];
  const hitRate = radarPayload.review_stats?.hit_rate;
  fields.appOverview.innerHTML = `
    <article class="command-tile">
      <span>监控目标</span>
      <strong>${enabled}/${targets.length}</strong>
    </article>
    <article class="command-tile">
      <span>已见商品</span>
      <strong>${seen}</strong>
    </article>
    <article class="command-tile accent">
      <span>雷达款式</span>
      <strong>${results.length}</strong>
    </article>
    <article class="command-tile warning">
      <span>采集缺口</span>
      <strong>${tasks.length}</strong>
    </article>
    <article class="command-tile good">
      <span>监控建议</span>
      <strong>${recommendations.length}</strong>
    </article>
    <article class="command-tile">
      <span>复盘命中</span>
      <strong>${formatPercent(hitRate)}</strong>
    </article>
  `;
}

function renderRadarPayload(payload) {
  state.radarItems = payload.items || [];
  state.radarSamples = payload.samples || [];
  state.radarReviews = payload.reviews || [];
  renderRadarItemOptions();
  renderRadarOverview(payload);
  renderAggregates(payload.aggregates || []);
  renderReleaseWatch(payload.release_watch || []);
  renderCollectionTasks(payload.collection_tasks || []);
  renderWatchRecommendations(payload.watch_recommendations || []);
  renderReviews(state.radarReviews, payload.review_stats || {});
  renderRadar(payload.results || []);
  renderSampleLedger(state.radarSamples);
}

function renderRadarItemOptions() {
  const selectedEdit = fields.radarEditItemId.value;
  const selectedSample = fields.radarSampleItemId.value;
  const selectedWatch = fields.radarWatchItemId.value;
  const selectedReview = fields.radarReviewItemId.value;
  const selectedCollect = fields.radarCollectItemId.value;
  fields.radarEditItemId.innerHTML = "";
  fields.radarSampleItemId.innerHTML = "";
  fields.radarWatchItemId.innerHTML = "";
  fields.radarReviewItemId.innerHTML = "";
  fields.radarCollectItemId.innerHTML = "";
  if (!state.radarItems.length) {
    const editOption = document.createElement("option");
    editOption.value = "";
    editOption.textContent = "暂无款式";
    fields.radarEditItemId.appendChild(editOption);
    [fields.radarSampleItemId, fields.radarWatchItemId, fields.radarReviewItemId, fields.radarCollectItemId].forEach((select) => {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "先添加款式";
      select.appendChild(option);
    });
    return;
  }
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "选择款式";
  fields.radarEditItemId.appendChild(placeholder);
  state.radarItems.forEach((item) => {
    const editOption = document.createElement("option");
    editOption.value = item.id;
    editOption.textContent = item.label || `#${item.id}`;
    fields.radarEditItemId.appendChild(editOption);
    [fields.radarSampleItemId, fields.radarWatchItemId, fields.radarReviewItemId, fields.radarCollectItemId].forEach((select) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label || `#${item.id}`;
      select.appendChild(option);
    });
  });
  if ([...fields.radarEditItemId.options].some((option) => option.value === selectedEdit)) {
    fields.radarEditItemId.value = selectedEdit;
  }
  if ([...fields.radarSampleItemId.options].some((option) => option.value === selectedSample)) {
    fields.radarSampleItemId.value = selectedSample;
  }
  if ([...fields.radarWatchItemId.options].some((option) => option.value === selectedWatch)) {
    fields.radarWatchItemId.value = selectedWatch;
  }
  if ([...fields.radarReviewItemId.options].some((option) => option.value === selectedReview)) {
    fields.radarReviewItemId.value = selectedReview;
  }
  if ([...fields.radarCollectItemId.options].some((option) => option.value === selectedCollect)) {
    fields.radarCollectItemId.value = selectedCollect;
  }
}

function renderRadarOverview(payload) {
  const results = payload.results || [];
  const tasks = payload.collection_tasks || [];
  const recommendations = payload.watch_recommendations || [];
  const releases = payload.release_watch || [];
  const reviews = payload.reviews || [];
  const stats = payload.review_stats || {};
  const priorityA = results.filter((item) => item.priority_band === "A").length;
  const priorityB = results.filter((item) => item.priority_band === "B").length;
  const upcoming = releases.filter(
    (item) => item.days_until !== null && item.days_until !== undefined && Number(item.days_until) >= 0 && Number(item.days_until) <= 45
  ).length;
  const topScore = results.length ? results[0].attention_score : null;
  fields.radarOverview.innerHTML = `
    <article class="overview-tile signal">
      <strong>${results.length}</strong>
      <span>雷达款式</span>
    </article>
    <article class="overview-tile ${priorityA ? "warning" : ""}">
      <strong>${priorityA}/${priorityB}</strong>
      <span>A/B 关注</span>
    </article>
    <article class="overview-tile">
      <strong>${formatNumber(topScore)}</strong>
      <span>最高关注分</span>
    </article>
    <article class="overview-tile ${tasks.length ? "warning" : ""}">
      <strong>${tasks.length}</strong>
      <span>采集缺口</span>
    </article>
    <article class="overview-tile ${recommendations.length ? "good" : ""}">
      <strong>${recommendations.length}</strong>
      <span>监控建议</span>
    </article>
    <article class="overview-tile">
      <strong>${upcoming}</strong>
      <span>45 天内发售</span>
    </article>
    <article class="overview-tile">
      <strong>${payload.samples?.length || 0}</strong>
      <span>价格样本</span>
    </article>
    <article class="overview-tile ${reviews.length ? "good" : ""}">
      <strong>${formatPercent(stats.hit_rate)}</strong>
      <span>复盘命中率</span>
    </article>
  `;
}

function renderRadar(results) {
  if (!results.length) {
    fields.radarList.innerHTML = '<div class="empty">暂无溢价数据</div>';
    return;
  }
  fields.radarList.innerHTML = "";
  results.forEach((result) => {
    const card = document.createElement("article");
    card.className = "radar-card";
    card.innerHTML = `
      <div class="priority-badge priority-${escapeHtml(result.priority_band)}">${escapeHtml(result.priority_band)}</div>
      <div class="radar-main">
        <h3>${escapeHtml(result.label)}</h3>
        <div class="radar-stats">
          <span>关注分 ${formatNumber(result.attention_score)}</span>
          <span>溢价率 ${formatPercent(result.premium_ratio)}</span>
          <span>到手 ¥${formatNumber(result.landed_cost_cny)}</span>
          <span>中位 ¥${formatNumber(result.market_median_cny)}</span>
          <span>淘宝/代购 ¥${formatNumber(result.domestic_median_cny)}</span>
          <span>加价 ${formatPercent(result.domestic_markup_ratio)}</span>
          <span>样本 ${result.sample_count}</span>
          ${result.release_date ? `<span>发售 ${escapeHtml(result.release_date)}</span>` : ""}
        </div>
      </div>
    `;
    fields.radarList.appendChild(card);
  });
}

function renderAggregates(aggregates) {
  fields.aggregateCount.textContent = `${aggregates.length} 组`;
  if (!aggregates.length) {
    fields.aggregateList.innerHTML = '<div class="empty">暂无品牌/系列聚合数据</div>';
    return;
  }
  fields.aggregateList.innerHTML = "";
  aggregates.forEach((aggregate) => {
    const card = document.createElement("article");
    card.className = "aggregate-card";
    card.innerHTML = `
      <div class="priority-badge priority-${escapeHtml(aggregate.priority_band)}">${escapeHtml(aggregate.priority_band)}</div>
      <div class="aggregate-main">
        <h3>${escapeHtml(aggregate.name)}</h3>
        <div class="radar-stats">
          <span>${aggregate.group_type === "brand" ? "品牌" : "系列"}</span>
          <span>关注分 ${formatNumber(aggregate.attention_score)}</span>
          <span>均分 ${formatNumber(aggregate.average_attention_score)}</span>
          <span>最佳 ${formatNumber(aggregate.max_attention_score)}</span>
          <span>溢价 ${formatPercent(aggregate.median_premium_ratio)}</span>
          <span>加价 ${formatPercent(aggregate.median_domestic_markup_ratio)}</span>
          <span>款式 ${aggregate.item_count}</span>
          <span>样本 ${aggregate.secondhand_sample_count}</span>
        </div>
      </div>
    `;
    fields.aggregateList.appendChild(card);
  });
}

function renderReleaseWatch(items) {
  fields.releaseWatchCount.textContent = `${items.length} 个`;
  if (!items.length) {
    fields.releaseWatchList.innerHTML = '<div class="empty">暂无发售日期</div>';
    return;
  }
  fields.releaseWatchList.innerHTML = "";
  items.forEach((item) => {
    const card = document.createElement("article");
    const sourceUrl = safeUrl(item.source_url);
    card.className = "release-card";
    card.innerHTML = `
      <div class="priority-badge priority-${escapeHtml(item.priority_band)}">${escapeHtml(item.priority_band)}</div>
      <div class="release-main">
        <h3>${escapeHtml(item.label)}</h3>
        <div class="release-meta">
          <span>${escapeHtml(item.release_date)}</span>
          <span>${escapeHtml(releaseStatusText(item))}</span>
          <span>关注分 ${formatNumber(item.attention_score)}</span>
          ${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">来源</a>` : ""}
        </div>
      </div>
    `;
    fields.releaseWatchList.appendChild(card);
  });
}

function renderCollectionTasks(tasks) {
  fields.collectionTaskCount.textContent = `${tasks.length} 项`;
  if (!tasks.length) {
    fields.collectionTaskList.innerHTML = '<div class="empty">暂无采集缺口</div>';
    return;
  }
  fields.collectionTaskList.innerHTML = "";
  tasks.slice(0, 12).forEach((task) => {
    const card = document.createElement("article");
    card.className = "collection-card";
    card.innerHTML = `
      <div class="priority-badge priority-${escapeHtml(task.priority_band)}">${escapeHtml(task.priority_band)}</div>
      <div class="collection-main">
        <h3>${escapeHtml(task.title)} · ${escapeHtml(task.label)}</h3>
        <p>${escapeHtml(task.reason)}</p>
        <div class="release-meta">
          <span>优先 ${formatNumber(task.priority_score)}</span>
          <span>${escapeHtml(task.task_type)}</span>
          <span>${escapeHtml(task.action_hint)}</span>
        </div>
        <button
          type="button"
          class="scan-button collection-action"
          data-item-id="${task.item_id}"
          data-action-type="${escapeHtml(task.action_type)}"
          data-source-type="${escapeHtml(task.suggested_source_type || "")}"
        >${escapeHtml(task.action_label || "处理")}</button>
      </div>
    `;
    fields.collectionTaskList.appendChild(card);
  });
}

function renderWatchRecommendations(items) {
  fields.watchRecommendationCount.textContent = `${items.length} 项`;
  if (!items.length) {
    fields.watchRecommendationList.innerHTML = '<div class="empty">暂无监控建议</div>';
    return;
  }
  fields.watchRecommendationList.innerHTML = "";
  items.slice(0, 12).forEach((item) => {
    const card = document.createElement("article");
    card.className = `watch-card ${item.already_watched ? "watched" : ""}`;
    card.innerHTML = `
      <div class="priority-badge priority-${escapeHtml(item.priority_band)}">${escapeHtml(item.priority_band)}</div>
      <div class="watch-main">
        <h3>${escapeHtml(item.label)}</h3>
        <p>${escapeHtml(item.reason || "建议加入上新监控")}</p>
        <div class="release-meta">
          <span>优先 ${formatNumber(item.priority_score)}</span>
          ${item.release_date ? `<span>发售 ${escapeHtml(item.release_date)}</span>` : ""}
          ${item.suggested_price_max ? `<span>建议上限 ¥${formatNumber(item.suggested_price_max)}</span>` : ""}
          ${item.already_watched ? `<span>已监控</span>` : ""}
        </div>
        <button
          type="button"
          class="scan-button watch-action"
          data-item-id="${item.item_id}"
          data-price-max="${item.suggested_price_max ?? ""}"
          ${item.already_watched ? "disabled" : ""}
        >${escapeHtml(item.action_label || "载入监控")}</button>
      </div>
    `;
    fields.watchRecommendationList.appendChild(card);
  });
}

function renderReviews(reviews, stats) {
  fields.reviewCount.textContent = `${reviews.length} 条`;
  fields.reviewStats.innerHTML = `
    <span>命中 ${stats.hit_count || 0}</span>
    <span>未命中 ${stats.miss_count || 0}</span>
    <span>待观察 ${stats.pending_count || 0}</span>
    <span>命中率 ${formatPercent(stats.hit_rate)}</span>
    <span>观察均值 ${formatPercent(stats.average_observed_premium_ratio)}</span>
    <span>预测均值 ${formatPercent(stats.average_predicted_premium_ratio)}</span>
  `;
  if (!reviews.length) {
    fields.reviewList.innerHTML = '<div class="empty">暂无复盘记录</div>';
    return;
  }
  fields.reviewList.innerHTML = "";
  reviews.forEach((review) => {
    const card = document.createElement("article");
    card.className = "review-card";
    card.innerHTML = `
      <div class="priority-badge priority-${escapeHtml(reviewStatusBand(review.review_status))}">
        ${escapeHtml(reviewStatusText(review.review_status))}
      </div>
      <div class="review-main">
        <h3>${escapeHtml(review.item_label)}</h3>
        <div class="release-meta">
          <span>窗口 ${review.review_window_days ?? "-"} 天</span>
          <span>观察 ¥${formatNumber(review.observed_price_cny)}</span>
          <span>观察溢价 ${formatPercent(review.observed_premium_ratio)}</span>
          <span>预测溢价 ${formatPercent(review.predicted_premium_ratio)}</span>
          <span>预测分 ${formatNumber(review.predicted_attention_score)}</span>
        </div>
        ${review.notes ? `<p>${escapeHtml(review.notes)}</p>` : ""}
        <div class="review-actions">
          <button type="button" class="scan-button review-edit" data-item-id="${review.item_id}">载入</button>
          <button type="button" class="danger-button review-delete" data-item-id="${review.item_id}">删除</button>
        </div>
      </div>
    `;
    fields.reviewList.appendChild(card);
  });
}

function renderSampleLedger(samples) {
  fields.sampleLedgerCount.textContent = `${samples.length} 条`;
  if (!samples.length) {
    fields.sampleLedgerList.innerHTML = '<div class="empty">暂无价格样本</div>';
    return;
  }
  fields.sampleLedgerList.innerHTML = "";
  samples.forEach((sample) => {
    const row = document.createElement("article");
    row.className = "sample-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(sample.item_label)}</strong>
        <span>${escapeHtml(sample.source_type)} · ${escapeHtml(sample.listing_status)} · ¥${formatNumber(sample.effective_price_cny)}</span>
        <small>${escapeHtml(sample.title || sample.source_url || "无标题")}</small>
      </div>
      <div class="sample-actions">
        <button type="button" class="scan-button sample-edit" data-sample-id="${sample.id}">编辑</button>
        <button type="button" class="danger-button sample-delete" data-sample-id="${sample.id}">删除</button>
      </div>
    `;
    fields.sampleLedgerList.appendChild(row);
  });
}

function currentTarget() {
  return (state.config.targets || [])[state.selectedTarget] || null;
}

function persistCurrentForm() {
  if (!state.config) return;
  const target = currentTarget();
  if (target) {
    target.name = fields.targetName.value.trim();
    target.url = fields.targetUrl.value.trim();
    target.enabled = fields.targetEnabled.checked;
    target.include_keywords = parseKeywords(fields.includeKeywords.value);
    target.exclude_keywords = parseKeywords(fields.excludeKeywords.value);
    target.price_min = parseNumber(fields.priceMin.value);
    target.price_max = parseNumber(fields.priceMax.value);
  }

  state.config.poll_interval_seconds = Number(fields.pollInterval.value || 90);
  state.config.notify_on_first_scan = fields.notifyOnFirstScan.checked;
  state.config.fetcher = {
    ...(state.config.fetcher || {}),
    type: fields.fetcherType.value,
    timeout_seconds: Number(fields.timeoutSeconds.value || 20),
    user_agent: fields.userAgent.value.trim(),
    wait_seconds: Number(fields.waitSeconds.value || 5),
    user_data_dir: fields.userDataDir.value.trim() || ".browser-profile",
  };
  state.config.notifications = buildNotifications();
}

function parseKeywords(value) {
  return value
    .split(/[\n,，]/)
    .map((keyword) => keyword.trim())
    .filter(Boolean);
}

function parseNumber(value) {
  if (value === "" || value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function getWebhook() {
  return (state.config.notifications || []).find((item) => item.type === "webhook") || null;
}

function buildNotifications() {
  const notifications = [{ type: "console", enabled: true }];
  notifications.push({
    type: "webhook",
    enabled: fields.webhookEnabled.checked,
    url: fields.webhookUrl.value.trim(),
  });
  return notifications;
}

async function saveConfig() {
  persistCurrentForm();
  await api("/api/config", {
    method: "POST",
    body: JSON.stringify({ config: state.config }),
  });
  toast("配置已保存");
  await loadAll();
}

async function scanNow() {
  persistCurrentForm();
  fields.scanStatus.textContent = "扫描中";
  fields.resultList.innerHTML = "";
  await saveConfig();
  const payload = await api("/api/scan", {
    method: "POST",
    body: JSON.stringify({ send_notifications: true }),
  });
  renderScanResults(payload);
  fields.scanStatus.textContent = "扫描完成";
  const statePayload = await api("/api/state");
  renderSummaryFromState(statePayload);
}

function renderScanResults(payload) {
  const results = payload.results || [];
  if (results.length === 0) {
    fields.resultList.innerHTML = '<div class="empty">没有扫描结果</div>';
    return;
  }
  fields.resultList.innerHTML = "";
  results.forEach((result) => {
    const block = document.createElement("article");
    block.className = "result-block";
    if (result.error) {
      block.innerHTML = `
        <header><h3>${escapeHtml(result.target)}</h3><small>失败</small></header>
        <div class="empty">${escapeHtml(result.error)}</div>
      `;
      fields.resultList.appendChild(block);
      return;
    }
    if (result.skipped) {
      block.innerHTML = `
        <header><h3>${escapeHtml(result.target)}</h3><small>停用</small></header>
      `;
      fields.resultList.appendChild(block);
      return;
    }
    const items = result.items || [];
    block.innerHTML = `
      <header>
        <h3>${escapeHtml(result.target)}</h3>
        <small>发现 ${result.found} · 匹配 ${result.matched} · 新增 ${result.new}</small>
      </header>
      ${items.length ? "" : '<div class="empty">没有新增匹配商品</div>'}
    `;
    items.forEach((item) => {
      const link = document.createElement("a");
      link.className = "item-link";
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = `${item.title}${item.price ? ` · ¥${item.price}` : ""}`;
      block.appendChild(link);
    });
    if (result.baseline_only) {
      const note = document.createElement("div");
      note.className = "empty";
      note.textContent = "首次扫描已建立基线，未发送提醒";
      block.appendChild(note);
    }
    fields.resultList.appendChild(block);
  });
}

function addTarget() {
  persistCurrentForm();
  state.config.targets.push({
    name: `目标 ${state.config.targets.length + 1}`,
    enabled: true,
    url: "",
    include_keywords: [],
    exclude_keywords: [],
    price_min: null,
    price_max: null,
  });
  state.selectedTarget = state.config.targets.length - 1;
  renderTargets();
  renderForms();
}

function deleteTarget() {
  const targets = state.config.targets || [];
  if (targets.length === 0) return;
  targets.splice(state.selectedTarget, 1);
  state.selectedTarget = Math.max(0, state.selectedTarget - 1);
  renderTargets();
  renderForms();
}

function radarItemPayload() {
  return {
    brand_name: fields.radarBrandName.value.trim(),
    series_name: fields.radarSeriesName.value.trim(),
    item_name: fields.radarItemName.value.trim(),
    category: fields.radarCategory.value.trim(),
    colorway: fields.radarColorway.value.trim(),
    original_price_jpy: parseNumber(fields.radarOriginalPriceJpy.value),
    jpy_to_cny: parseNumber(fields.radarJpyToCny.value) ?? 0.05,
    japan_domestic_shipping_cny: parseNumber(fields.radarJapanShipping.value) ?? 0,
    international_shipping_cny: parseNumber(fields.radarInternationalShipping.value) ?? 0,
    proxy_fee_cny: parseNumber(fields.radarProxyFee.value) ?? 0,
    tax_or_buffer_cny: parseNumber(fields.radarTaxBuffer.value) ?? 0,
    release_signal_score: parseNumber(fields.radarReleaseSignalScore.value) ?? 50,
    release_date: fields.radarReleaseDate.value,
    source_url: fields.radarItemSourceUrl.value.trim(),
  };
}

async function saveRadarItem(event) {
  event.preventDefault();
  const editingId = fields.radarEditingItemId.value;
  const payload = radarItemPayload();
  if (editingId) {
    payload.item_id = editingId;
  }
  const endpoint = editingId ? "/api/radar/items/update" : "/api/radar/items";
  const response = await api(endpoint, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderRadarPayload(response);
  clearRadarItemForm();
  toast(editingId ? "款式已保存" : "款式已添加");
}

function loadSelectedRadarItem() {
  const item = findRadarItem(fields.radarEditItemId.value);
  if (!item) {
    toast("请选择款式");
    return;
  }
  fillRadarItemForm(item);
}

function fillRadarItemForm(item) {
  state.editingRadarItemId = item.id;
  fields.radarEditingItemId.value = item.id;
  fields.radarEditItemId.value = item.id;
  fields.radarBrandName.value = item.brand_name || "";
  fields.radarSeriesName.value = item.series_name || "";
  fields.radarItemName.value = item.item_name || "";
  fields.radarCategory.value = item.category || "";
  fields.radarColorway.value = item.colorway || "";
  fields.radarOriginalPriceJpy.value = item.original_price_jpy ?? "";
  fields.radarJpyToCny.value = item.jpy_to_cny ?? "0.05";
  fields.radarReleaseSignalScore.value = item.release_signal_score ?? "50";
  fields.radarReleaseDate.value = item.release_date || "";
  fields.radarJapanShipping.value = item.japan_domestic_shipping_cny ?? "";
  fields.radarInternationalShipping.value = item.international_shipping_cny ?? "";
  fields.radarProxyFee.value = item.proxy_fee_cny ?? "";
  fields.radarTaxBuffer.value = item.tax_or_buffer_cny ?? "";
  fields.radarItemSourceUrl.value = item.source_url || "";
  fields.addRadarItemBtn.textContent = "保存修改";
}

function clearRadarItemForm() {
  state.editingRadarItemId = null;
  fields.radarItemForm.reset();
  fields.radarEditingItemId.value = "";
  fields.radarEditItemId.value = "";
  fields.radarJpyToCny.value = "0.05";
  fields.radarReleaseSignalScore.value = "50";
  fields.addRadarItemBtn.textContent = "添加款式";
}

async function deleteSelectedRadarItem() {
  const itemId = fields.radarEditingItemId.value || fields.radarEditItemId.value;
  if (!itemId) {
    toast("请选择款式");
    return;
  }
  const response = await api("/api/radar/items/delete", {
    method: "POST",
    body: JSON.stringify({ item_id: itemId }),
  });
  renderRadarPayload(response);
  clearRadarItemForm();
  toast(response.deleted ? "款式已删除" : "款式不存在");
}

function findRadarItem(itemId) {
  const id = Number(itemId);
  return state.radarItems.find((item) => Number(item.id) === id) || null;
}

function radarSamplePayload() {
  return {
    item_id: fields.radarSampleItemId.value,
    source_type: fields.radarSampleSourceType.value,
    listed_price_cny: parseNumber(fields.radarListedPriceCny.value),
    sold_price_cny: parseNumber(fields.radarSoldPriceCny.value),
    listing_status: fields.radarListingStatus.value,
    condition: fields.radarCondition.value.trim(),
    confidence: parseNumber(fields.radarConfidence.value) ?? 0.7,
    title: fields.radarSampleTitle.value.trim(),
    source_url: fields.radarSampleSourceUrl.value.trim(),
  };
}

async function saveRadarSample(event) {
  event.preventDefault();
  const editingId = fields.radarEditingSampleId.value;
  const payload = radarSamplePayload();
  if (editingId) {
    payload.sample_id = editingId;
  }
  const endpoint = editingId ? "/api/radar/samples/update" : "/api/radar/samples";
  const response = await api(endpoint, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderRadarPayload(response);
  clearRadarSampleForm();
  toast(editingId ? "样本已保存" : "样本已添加");
}

function fillRadarSampleForm(sample) {
  state.editingRadarSampleId = sample.id;
  fields.radarEditingSampleId.value = sample.id;
  fields.radarSampleItemId.value = sample.item_id;
  fields.radarSampleSourceType.value = sample.source_type || "xianyu";
  fields.radarListingStatus.value = sample.listing_status || "listed";
  fields.radarListedPriceCny.value = sample.listed_price_cny ?? "";
  fields.radarSoldPriceCny.value = sample.sold_price_cny ?? "";
  fields.radarCondition.value = sample.condition || "";
  fields.radarConfidence.value = sample.confidence ?? "0.7";
  fields.radarSampleTitle.value = sample.title || "";
  fields.radarSampleSourceUrl.value = sample.source_url || "";
  fields.addRadarSampleBtn.textContent = "保存样本";
}

function clearRadarSampleForm() {
  state.editingRadarSampleId = null;
  const selectedItem = fields.radarSampleItemId.value;
  fields.radarSampleForm.reset();
  fields.radarEditingSampleId.value = "";
  if ([...fields.radarSampleItemId.options].some((option) => option.value === selectedItem)) {
    fields.radarSampleItemId.value = selectedItem;
  }
  fields.radarConfidence.value = "0.7";
  fields.addRadarSampleBtn.textContent = "添加样本";
}

function editRadarSample(sampleId) {
  const sample = findRadarSample(sampleId);
  if (!sample) {
    toast("样本不存在");
    return;
  }
  fillRadarSampleForm(sample);
}

function findRadarSample(sampleId) {
  const id = Number(sampleId);
  return state.radarSamples.find((sample) => Number(sample.id) === id) || null;
}

function runCollectionTaskAction(button) {
  const item = findRadarItem(button.dataset.itemId);
  if (!item) {
    toast("款式不存在");
    return;
  }
  const actionType = button.dataset.actionType || "edit_item";
  if (actionType === "add_sample") {
    clearRadarSampleForm();
    fields.radarSampleItemId.value = item.id;
    const sourceType = button.dataset.sourceType || "xianyu";
    if ([...fields.radarSampleSourceType.options].some((option) => option.value === sourceType)) {
      fields.radarSampleSourceType.value = sourceType;
    }
    fields.radarListingStatus.value = "listed";
    fields.radarSampleTitle.value = item.label || "";
    if ([...fields.radarCollectItemId.options].some((option) => option.value === String(item.id))) {
      fields.radarCollectItemId.value = item.id;
      fields.radarCollectSourceType.value = sourceType;
    }
    fields.radarSampleForm.scrollIntoView({ behavior: "smooth", block: "start" });
    toast("已载入价格样本表单");
    return;
  }
  fillRadarItemForm(item);
  fields.radarItemForm.scrollIntoView({ behavior: "smooth", block: "start" });
  toast("已载入款式表单");
}

function loadWatchRecommendation(button) {
  const item = findRadarItem(button.dataset.itemId);
  if (!item) {
    toast("款式不存在");
    return;
  }
  fields.radarWatchItemId.value = item.id;
  fields.radarWatchPriceMax.value = button.dataset.priceMax || "";
  fields.radarWatchForm.scrollIntoView({ behavior: "smooth", block: "start" });
  toast("已载入监控表单");
}

function radarReviewPayload() {
  return {
    item_id: fields.radarReviewItemId.value,
    review_status: fields.radarReviewStatus.value,
    observed_price_cny: parseNumber(fields.radarObservedPriceCny.value),
    review_window_days: parseNumber(fields.radarReviewWindowDays.value),
    notes: fields.radarReviewNotes.value.trim(),
  };
}

async function saveRadarReview(event) {
  event.preventDefault();
  const response = await api("/api/radar/reviews", {
    method: "POST",
    body: JSON.stringify(radarReviewPayload()),
  });
  renderRadarPayload(response);
  toast("复盘已保存");
}

function fillRadarReviewForm(review) {
  fields.radarReviewItemId.value = review.item_id;
  fields.radarReviewStatus.value = review.review_status || "pending";
  fields.radarObservedPriceCny.value = review.observed_price_cny ?? "";
  fields.radarReviewWindowDays.value = review.review_window_days ?? "";
  fields.radarReviewNotes.value = review.notes || "";
  fields.radarReviewForm.scrollIntoView({ behavior: "smooth", block: "start" });
}

function editRadarReview(itemId) {
  const review = findRadarReview(itemId);
  if (!review) {
    toast("复盘记录不存在");
    return;
  }
  fillRadarReviewForm(review);
  toast("已载入复盘表单");
}

function findRadarReview(itemId) {
  const id = Number(itemId);
  return state.radarReviews.find((review) => Number(review.item_id) === id) || null;
}

async function deleteRadarReview(itemId) {
  const response = await api("/api/radar/reviews/delete", {
    method: "POST",
    body: JSON.stringify({ item_id: itemId }),
  });
  renderRadarPayload(response);
  toast(response.deleted ? "复盘已删除" : "复盘不存在");
}

async function importRadarCsv(event) {
  event.preventDefault();
  const response = await api("/api/radar/import", {
    method: "POST",
    body: JSON.stringify({ path: fields.radarImportPath.value.trim() }),
  });
  renderRadarPayload(response);
  const summary = response.import;
  if (summary) {
    toast(`导入 ${summary.rows_read} 行，新增 ${summary.items_created} 款，${summary.samples_created} 样本`);
  } else {
    toast("CSV 已导入");
  }
}

async function createWatchTarget(event) {
  event.preventDefault();
  const payload = {
    item_id: fields.radarWatchItemId.value,
    url: fields.radarWatchUrl.value.trim(),
    price_max: parseNumber(fields.radarWatchPriceMax.value),
  };
  const response = await api("/api/radar/watch-target", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (response.state) {
    renderSummaryFromState(response.state);
  }
  await loadAll();
  toast(response.created ? "监控目标已生成" : "监控目标已存在");
}

async function collectRadarSamples(event) {
  event.preventDefault();
  const response = await api("/api/radar/collect", {
    method: "POST",
    body: JSON.stringify({
      item_id: fields.radarCollectItemId.value,
      source_type: fields.radarCollectSourceType.value,
      listing_status: fields.radarCollectStatus.value,
      confidence: parseNumber(fields.radarCollectConfidence.value) ?? 0.65,
      text: fields.radarCollectText.value,
    }),
  });
  renderRadarPayload(response);
  fields.radarCollectText.value = "";
  const saved = response.collected?.saved_count || 0;
  toast(`已保存 ${saved} 条样本`);
}

async function collectReleaseItems(event) {
  event.preventDefault();
  const response = await api("/api/radar/release-collect", {
    method: "POST",
    body: JSON.stringify({
      brand_name: fields.radarReleaseCollectBrand.value.trim(),
      series_name: fields.radarReleaseCollectSeries.value.trim(),
      category: fields.radarReleaseCollectCategory.value.trim(),
      release_date: fields.radarReleaseCollectDate.value,
      source_url: fields.radarReleaseCollectUrl.value.trim(),
      jpy_to_cny: parseNumber(fields.radarReleaseCollectJpyToCny.value) ?? 0.05,
      release_signal_score: parseNumber(fields.radarReleaseCollectSignal.value) ?? 70,
      text: fields.radarReleaseCollectText.value,
    }),
  });
  renderRadarPayload(response);
  fields.radarReleaseCollectText.value = "";
  const summary = response.release_collected || {};
  toast(`已保存 ${summary.saved_count || 0} 个发售款式，新增 ${summary.created_count || 0} 个`);
}

async function deleteRadarSample(sampleId) {
  const response = await api("/api/radar/samples/delete", {
    method: "POST",
    body: JSON.stringify({ sample_id: sampleId }),
  });
  renderRadarPayload(response);
  toast(response.deleted ? "样本已删除" : "样本不存在");
}

function toast(message) {
  fields.toast.textContent = message;
  fields.toast.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => fields.toast.classList.remove("show"), 2200);
}

function formatNumber(value) {
  if (value === null || value === undefined) return "-";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function formatPercent(value) {
  if (value === null || value === undefined) return "-";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function releaseStatusText(item) {
  if (item.days_until === null || item.days_until === undefined) return "日期待确认";
  if (item.days_until === 0) return "今日发售";
  if (item.days_until > 0) return `${item.days_until} 天后`;
  return `${Math.abs(item.days_until)} 天前`;
}

function reviewStatusText(status) {
  if (status === "hit") return "命中";
  if (status === "miss") return "未中";
  return "待观";
}

function reviewStatusBand(status) {
  if (status === "hit") return "A";
  if (status === "miss") return "D";
  return "C";
}

function safeUrl(value) {
  const url = String(value || "").trim();
  return /^https?:\/\//i.test(url) ? url : "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    persistCurrentForm();
    state.activeTab = tab.dataset.tab;
    renderForms();
  });
});

el("reloadBtn").addEventListener("click", () => loadAll().then(() => toast("已重新加载")));
el("saveBtn").addEventListener("click", () => saveConfig().catch((error) => toast(error.message)));
el("scanBtn").addEventListener("click", () => scanNow().catch((error) => {
  fields.scanStatus.textContent = "扫描失败";
  toast(error.message);
}));
el("addTargetBtn").addEventListener("click", addTarget);
el("deleteTargetBtn").addEventListener("click", deleteTarget);
el("refreshRadarBtn").addEventListener("click", () => loadRadar().then(() => toast("雷达已刷新")));
fields.loadRadarItemBtn.addEventListener("click", loadSelectedRadarItem);
fields.newRadarItemBtn.addEventListener("click", clearRadarItemForm);
fields.deleteRadarItemBtn.addEventListener("click", () => deleteSelectedRadarItem().catch((error) => toast(error.message)));
fields.radarItemForm.addEventListener("submit", (event) => saveRadarItem(event).catch((error) => toast(error.message)));
fields.newRadarSampleBtn.addEventListener("click", clearRadarSampleForm);
fields.radarSampleForm.addEventListener("submit", (event) => saveRadarSample(event).catch((error) => toast(error.message)));
fields.radarImportForm.addEventListener("submit", (event) => importRadarCsv(event).catch((error) => toast(error.message)));
fields.radarWatchForm.addEventListener("submit", (event) => createWatchTarget(event).catch((error) => toast(error.message)));
fields.radarReviewForm.addEventListener("submit", (event) => saveRadarReview(event).catch((error) => toast(error.message)));
fields.radarCollectForm.addEventListener("submit", (event) => collectRadarSamples(event).catch((error) => toast(error.message)));
fields.radarReleaseCollectForm.addEventListener("submit", (event) => collectReleaseItems(event).catch((error) => toast(error.message)));
fields.collectionTaskList.addEventListener("click", (event) => {
  if (!event.target.closest) return;
  const button = event.target.closest(".collection-action");
  if (!button) return;
  runCollectionTaskAction(button);
});
fields.watchRecommendationList.addEventListener("click", (event) => {
  if (!event.target.closest) return;
  const button = event.target.closest(".watch-action");
  if (!button) return;
  loadWatchRecommendation(button);
});
fields.reviewList.addEventListener("click", (event) => {
  if (!event.target.closest) return;
  const editButton = event.target.closest(".review-edit");
  if (editButton) {
    editRadarReview(editButton.dataset.itemId);
    return;
  }
  const deleteButton = event.target.closest(".review-delete");
  if (!deleteButton) return;
  deleteRadarReview(deleteButton.dataset.itemId).catch((error) => toast(error.message));
});
fields.sampleLedgerList.addEventListener("click", (event) => {
  if (!event.target.closest) return;
  const editButton = event.target.closest(".sample-edit");
  if (editButton) {
    editRadarSample(editButton.dataset.sampleId);
    return;
  }
  const deleteButton = event.target.closest(".sample-delete");
  if (!deleteButton) return;
  deleteRadarSample(deleteButton.dataset.sampleId).catch((error) => toast(error.message));
});

[
  fields.targetName,
  fields.targetUrl,
  fields.targetEnabled,
  fields.includeKeywords,
  fields.excludeKeywords,
  fields.priceMin,
  fields.priceMax,
].forEach((input) => {
  input.addEventListener("input", () => {
    persistCurrentForm();
    renderTargets();
  });
});

loadAll().catch((error) => toast(error.message));
