"use strict";

const elements = {
  categoryList: document.querySelector("#category-list"),
  currentCategory: document.querySelector("#current-category"),
  entries: document.querySelector("#entries"),
  fetchNotice: document.querySelector("#fetch-notice"),
  lastUpdated: document.querySelector("#last-updated"),
  minimumBookmarks: document.querySelector("#minimum-bookmarks"),
  domainSearch: document.querySelector("#domain-search"),
  titleSearch: document.querySelector("#title-search"),
  resetFilters: document.querySelector("#reset-filters"),
  resultCount: document.querySelector("#result-count"),
  sortButtons: [...document.querySelectorAll(".sort-button")],
  themeToggle: document.querySelector("#theme-toggle"),
};

const state = {
  data: null,
  category: "all",
  mode: "popular",
  minimumBookmarks: 3,
  domainQuery: "",
  titleQuery: "",
};

const dateFormatter = new Intl.DateTimeFormat("ja-JP", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const compactNumberFormatter = new Intl.NumberFormat("ja-JP");

function makeElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function safeHttpUrl(value) {
  try {
    const parsed = new URL(String(value));
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

function configureExternalLink(link, value) {
  const safeUrl = safeHttpUrl(value);
  if (!safeUrl) return false;
  link.href = safeUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return true;
}

function formatDate(value, fallback = "日時不明") {
  if (!value) return fallback;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? fallback : dateFormatter.format(parsed);
}

function categoryLabel(categoryId) {
  const category = state.data?.categories?.find((item) => item.id === categoryId);
  return category?.label || "総合";
}

function hasAppearance(article) {
  if (Array.isArray(article.appearances)) {
    return article.appearances.some(
      (appearance) =>
        appearance.category === state.category && appearance.mode === state.mode,
    );
  }
  return (
    article.categories?.includes(state.category) && article.modes?.includes(state.mode)
  );
}

function diversifyDomains(articles, maximumConsecutive = 2) {
  const remaining = [...articles];
  const diversified = [];
  let lastDomain = "";
  let streak = 0;

  while (remaining.length) {
    let selectedIndex = 0;
    if (streak >= maximumConsecutive) {
      const alternativeIndex = remaining.findIndex(
        (article) => article.domain !== lastDomain,
      );
      if (alternativeIndex >= 0) selectedIndex = alternativeIndex;
    }
    const [selected] = remaining.splice(selectedIndex, 1);
    if (selected.domain === lastDomain) {
      streak += 1;
    } else {
      lastDomain = selected.domain;
      streak = 1;
    }
    diversified.push(selected);
  }
  return diversified;
}

function filteredArticles() {
  if (!state.data) return [];
  const domainQuery = state.domainQuery.trim().toLocaleLowerCase("ja");
  const titleQuery = state.titleQuery.trim().toLocaleLowerCase("ja");
  const articles = state.data.articles.filter((article) => {
    const bookmarkCount = Number(article.bookmarkCount) || 0;
    const domain = String(article.domain || "").toLocaleLowerCase("ja");
    const title = String(article.title || "").toLocaleLowerCase("ja");
    return (
      hasAppearance(article) &&
      bookmarkCount >= state.minimumBookmarks &&
      (!domainQuery || domain.includes(domainQuery)) &&
      (!titleQuery || title.includes(titleQuery))
    );
  });

  articles.sort((left, right) => {
    if (state.mode === "popular") {
      const bookmarkDifference =
        (Number(right.bookmarkCount) || 0) - (Number(left.bookmarkCount) || 0);
      if (bookmarkDifference) return bookmarkDifference;
    }
    return new Date(right.publishedAt || right.lastSeenAt || 0) -
      new Date(left.publishedAt || left.lastSeenAt || 0);
  });
  return diversifyDomains(articles);
}

function createBookmarkScore(article) {
  const score = makeElement("div", "bookmark-score");
  const count = makeElement(
    "strong",
    "bookmark-count",
    compactNumberFormatter.format(Number(article.bookmarkCount) || 0),
  );
  const unit = makeElement("span", "bookmark-unit", "USERS");
  score.append(count, unit);
  return score;
}

function createEntryCard(article) {
  const card = makeElement("article", "entry-card");
  const body = makeElement("div", "entry-body");
  const meta = makeElement("div", "entry-meta");
  const domainButton = makeElement("button", "domain-chip", article.domain || "不明");
  domainButton.type = "button";
  domainButton.title = "このドメインで絞り込む";
  domainButton.addEventListener("click", () => {
    state.domainQuery = article.domain || "";
    elements.domainSearch.value = state.domainQuery;
    render();
  });
  const date = makeElement("time", "entry-date", formatDate(article.publishedAt));
  if (article.publishedAt) date.dateTime = article.publishedAt;
  meta.append(domainButton, date);

  const heading = makeElement("h3", "entry-title");
  const titleLink = makeElement("a", "entry-title-link", article.title || "タイトル不明");
  if (!configureExternalLink(titleLink, article.url)) {
    titleLink.removeAttribute("href");
  }
  heading.append(titleLink);

  const summary = makeElement(
    "p",
    "entry-summary",
    article.description || "概要は配信されていません。",
  );

  const footer = makeElement("div", "entry-footer");
  const articleUrl = makeElement("a", "article-url", article.url || "URL不明");
  articleUrl.title = article.url || "";
  configureExternalLink(articleUrl, article.url);
  const actions = makeElement("div", "entry-actions");
  const readLink = makeElement("a", "read-link", "記事を読む ↗");
  const commentLink = makeElement("a", "comment-link", "コメントを見る ↗");
  configureExternalLink(readLink, article.url);
  configureExternalLink(commentLink, article.commentUrl);
  actions.append(readLink, commentLink);
  footer.append(articleUrl, actions);

  body.append(meta, heading, summary, footer);
  card.append(createBookmarkScore(article), body);
  return card;
}

function createEmptyState() {
  const empty = makeElement("div", "empty-state");
  const mark = makeElement("span", "empty-mark", "0");
  const heading = makeElement("h3", "", "条件に合う記事がありません");
  const copy = makeElement(
    "p",
    "",
    "カテゴリーや最低ブックマーク数、検索条件を変えてみてください。",
  );
  const button = makeElement("button", "empty-reset", "絞り込みをリセット");
  button.type = "button";
  button.addEventListener("click", resetFilters);
  empty.append(mark, heading, copy, button);
  return empty;
}

function renderCategories() {
  const fragment = document.createDocumentFragment();
  for (const category of state.data?.categories || []) {
    const button = makeElement("button", "category-button", category.label);
    button.type = "button";
    button.dataset.category = category.id;
    button.classList.toggle("is-active", category.id === state.category);
    button.setAttribute("aria-pressed", String(category.id === state.category));
    button.addEventListener("click", () => {
      state.category = category.id;
      renderCategories();
      render();
    });
    fragment.append(button);
  }
  elements.categoryList.replaceChildren(fragment);
}

function render() {
  const articles = filteredArticles();
  elements.currentCategory.textContent = categoryLabel(state.category);
  elements.resultCount.textContent = compactNumberFormatter.format(articles.length);
  elements.entries.setAttribute("aria-busy", "false");
  if (!articles.length) {
    elements.entries.replaceChildren(createEmptyState());
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const article of articles) fragment.append(createEntryCard(article));
  elements.entries.replaceChildren(fragment);
}

function resetFilters() {
  state.minimumBookmarks = Number(state.data?.filters?.minimumBookmarkCount) || 0;
  state.domainQuery = "";
  state.titleQuery = "";
  elements.minimumBookmarks.value = String(state.minimumBookmarks);
  elements.domainSearch.value = "";
  elements.titleSearch.value = "";
  render();
}

function setMode(mode) {
  state.mode = mode;
  for (const button of elements.sortButtons) {
    const active = button.dataset.sort === mode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  render();
}

function currentTheme() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit) return explicit;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function updateThemeButton() {
  const dark = currentTheme() === "dark";
  elements.themeToggle.setAttribute(
    "aria-label",
    dark ? "ライトモードに切り替える" : "ダークモードに切り替える",
  );
  elements.themeToggle.querySelector(".theme-icon").textContent = dark ? "☼" : "◐";
  elements.themeToggle.querySelector(".theme-label").textContent = dark ? "ライト" : "ダーク";
}

function initializeTheme() {
  const stored = localStorage.getItem("hatebu-minus-theme");
  if (["light", "dark"].includes(stored)) {
    document.documentElement.dataset.theme = stored;
  }
  updateThemeButton();
  elements.themeToggle.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("hatebu-minus-theme", next);
    updateThemeButton();
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.dataset.theme) updateThemeButton();
  });
}

function bindControls() {
  for (const button of elements.sortButtons) {
    button.addEventListener("click", () => setMode(button.dataset.sort));
  }
  elements.minimumBookmarks.addEventListener("input", () => {
    state.minimumBookmarks = Math.max(0, Number(elements.minimumBookmarks.value) || 0);
    render();
  });
  elements.domainSearch.addEventListener("input", () => {
    state.domainQuery = elements.domainSearch.value;
    render();
  });
  elements.titleSearch.addEventListener("input", () => {
    state.titleQuery = elements.titleSearch.value;
    render();
  });
  elements.resetFilters.addEventListener("click", resetFilters);
}

function showLoadError(error) {
  elements.lastUpdated.textContent = "データを読み込めませんでした";
  elements.resultCount.textContent = "0";
  elements.entries.setAttribute("aria-busy", "false");
  const empty = makeElement("div", "empty-state error-state");
  empty.append(
    makeElement("span", "empty-mark", "!"),
    makeElement("h3", "", "記事データを読み込めませんでした"),
    makeElement("p", "", "しばらく待ってからページを再読み込みしてください。"),
  );
  elements.entries.replaceChildren(empty);
  console.error(error);
}

async function initialize() {
  initializeTheme();
  bindControls();
  try {
    const response = await fetch("./data/entries.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data || !Array.isArray(data.articles) || !Array.isArray(data.categories)) {
      throw new Error("Invalid data format");
    }
    state.data = data;
    state.minimumBookmarks = Number(data.filters?.minimumBookmarkCount) || 0;
    elements.minimumBookmarks.value = String(state.minimumBookmarks);
    elements.lastUpdated.textContent = formatDate(
      data.lastSuccessfulUpdateAt,
      "初回データ取得前",
    );
    if (data.lastSuccessfulUpdateAt) {
      elements.lastUpdated.dateTime = data.lastSuccessfulUpdateAt;
    }
    const failedFeeds = Number(data.fetchSummary?.failedFeeds) || 0;
    if (failedFeeds > 0) {
      elements.fetchNotice.hidden = false;
      elements.fetchNotice.textContent =
        `一部のRSS（${failedFeeds}件）を取得できなかったため、該当箇所は前回データを含みます。`;
    }
    renderCategories();
    render();
  } catch (error) {
    showLoadError(error);
  }
}

initialize();
