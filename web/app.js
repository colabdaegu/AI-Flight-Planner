// 항공 여행 일정 플래너 화면. 사용자 입력과 서버 응답은 모두 textContent 로만 넣는다 (XSS 방지).
const $ = (sel) => document.querySelector(sel);

// 움직임 줄이기 설정이면 비행기 장면을 멈춘 채로 둔다
if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) {
  document.querySelectorAll(".plane-go, .contrail").forEach((n) => n.remove());
}

const KR = ["ICN", "GMP", "PUS", "CJU", "TAE", "CJJ", "KWJ", "USN", "HIN", "RSU", "WJU", "YNY", "KPO", "KUV", "MWX"];
const TOOL_LABEL = {
  search_departures: "출발 편 조회", search_arrivals: "도착 편 조회", search_flight: "편명 조회",
  get_flight_detail: "편명 상세", get_airport_congestion: "공항 혼잡도", get_incheon_passenger_forecast: "인천 승객 예고",
  get_incheon_facilities: "인천 시설 안내", get_commercial_facilities: "상업시설 계약", get_airport_overview: "공항 개요",
  search_airports: "공항 검색",
};
const INTENT_LABEL = {
  departures: "출발 편 찾기", arrivals: "도착 편 찾기", flight_detail: "편명 조회", congestion: "혼잡도",
  passenger: "예상 승객", facilities: "시설 안내", plan: "계획 안내", greeting: "인사·사용법", unknown: "알 수 없음",
};
const STEP_LABEL = { analyze: "의도 분석", plan: "계획", execute: "도구 실행", judge: "판단", enrich: "상세 보강", respond: "응답" };

// ---------------- 공통 ----------------
function todayKst() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
}
function ymdOf(input) {
  return input.value ? input.value.replaceAll("-", "") : "";
}
function fmtYmd(ymd) {
  return ymd && ymd.length === 8 ? `${ymd.slice(0, 4)}-${ymd.slice(4, 6)}-${ymd.slice(6, 8)}` : (ymd || "");
}
function hhmm(s) {
  return s && s.length >= 16 ? s.slice(11, 16) : (s || "-");
}
function dateOf(s) {
  return s && s.length >= 10 ? s.slice(0, 10) : "";
}
function fmtArgs(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}=${v}`).join(", ");
}
function kstHour() {
  return Number(new Date().toLocaleString("en-US", { hour: "numeric", hour12: false, timeZone: "Asia/Seoul" })) % 24;
}

async function api(path, opts = {}) {
  const init = { ...opts, headers: { "Content-Type": "application/json" } };
  try {
    const res = await fetch(`/api${path}`, init);
    return await res.json();
  } catch (e) {
    return { ok: false, error: { code: "NETWORK", message: "서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인하십시오." } };
  }
}

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function button(text, cls, onClick) {
  const b = el("button", text, cls);
  b.type = "button";
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

// ---------------- 탭 ----------------
function showTab(name) {
  // 내 계획 탭을 떠나면 입력·수정 중이던 내용을 처음 상태로 비운다
  if (name !== "plans" && $("#tab-plans").classList.contains("active")) resetPlanForm();
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  const btn = document.querySelector(`.tabs button[data-tab="${name}"]`);
  if (btn) {
    $("#headline").textContent = btn.dataset.title;
    $("#subline").textContent = btn.dataset.sub;
  }
  if (name === "plans") loadPlans();
  if (name === "logs") loadLogs();
  if (name === "facility" && !fac.data) loadFacility(fac.code);
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------------- 공항 자동 완성 ----------------
// 입력칸 + 숨은 값(name, 실제 공항 코드) + 추천 목록을 host 안에 만든다.
function createAirportField(host, { scope, name = "airport", placeholder = "공항 이름 또는 코드 (예: 김포, 도쿄, NRT)" }) {
  host.replaceChildren();
  const input = el("input");
  input.type = "text";
  input.placeholder = placeholder;
  input.autocomplete = "off";
  input.maxLength = 40;
  const hidden = el("input");
  hidden.type = "hidden";
  hidden.name = name;
  const list = el("ul", null, "suggest");
  list.setAttribute("role", "listbox");
  list.hidden = true;
  host.append(input, hidden, list);

  let items = [];
  let active = -1;
  let timer = null;
  let seq = 0;

  function closeList() {
    list.hidden = true;
    active = -1;
  }
  function setValue(code, label) {
    hidden.value = code || "";
    input.value = code ? label : "";
    closeList();
  }
  function highlight() {
    [...list.children].forEach((li, i) => li.classList.toggle("on", i === active));
  }
  function choose(i) {
    const a = items[i];
    if (a) setValue(a.code, `${a.name} (${a.code})`);
  }
  function render(results) {
    items = results;
    active = -1;
    list.replaceChildren();
    if (!results.length) {
      list.hidden = true;
      return;
    }
    results.forEach((a, i) => {
      const li = el("li", null, "opt");
      li.setAttribute("role", "option");
      li.append(el("span", a.name, "opt-name"), el("span", a.code, "opt-code"), el("span", a.en, "opt-en"));
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        choose(i);
      });
      list.appendChild(li);
    });
    list.hidden = false;
  }
  async function search() {
    const q = input.value.trim();
    const mine = ++seq;
    if (!q) {
      render([]);
      return;
    }
    const body = await api(`/airports?q=${encodeURIComponent(q)}&scope=${scope}`);
    if (mine !== seq) return;
    render(body.ok ? body.data : []);
  }

  input.addEventListener("input", () => {
    hidden.value = "";
    clearTimeout(timer);
    timer = setTimeout(search, 150);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden || !items.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      active = (active + 1) % items.length;
      highlight();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      active = (active - 1 + items.length) % items.length;
      highlight();
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      choose(active);
    } else if (e.key === "Escape") {
      closeList();
    }
  });
  input.addEventListener("blur", () => setTimeout(closeList, 120));

  async function resolve() {
    if (hidden.value) return hidden.value;
    const q = input.value.trim();
    if (!q) return "";
    const up = q.toUpperCase();
    const body = await api(`/airports?q=${encodeURIComponent(q)}&scope=${scope}`);
    const found = body.ok ? body.data : [];
    const pick = found.find((a) => a.code === up) || found[0];
    if (pick) {
      setValue(pick.code, `${pick.name} (${pick.code})`);
      return pick.code;
    }
    if (scope === "all" && /^[A-Z]{3}$/.test(up)) {
      setValue(up, up);
      return up;
    }
    throw new Error(`공항을 찾을 수 없습니다: ${q}`);
  }

  return { resolve, set: setValue };
}

// ---------------- 날짜 기본값 ----------------
["#dep-date", "#arr-date", "#flt-date"].forEach((sel) => {
  const input = $(sel);
  input.value = todayKst();
  input.min = todayKst();
  input.max = "9999-12-31";
});
// 시각: 1시간 단위, 기본은 지금 시간대 (16:20 → 16:00)
["#dep-hour", "#arr-hour"].forEach((sel) => {
  const box = $(sel);
  for (let h = 0; h < 24; h += 1) {
    const o = el("option", `${h}시`);
    o.value = `${String(h).padStart(2, "0")}00`;
    box.appendChild(o);
  }
  box.value = `${String(kstHour()).padStart(2, "0")}00`;
});

const depOrigin = createAirportField($("#dep-origin"), { scope: "origin", name: "origin" });
depOrigin.set("ICN", "인천 (ICN)");
const depDest = createAirportField($("#dep-dest"), { scope: "all", name: "destination", placeholder: "예: 도쿄, NRT" });
const arrAirport = createAirportField($("#arr-airport"), { scope: "origin", name: "airport" });
arrAirport.set("ICN", "인천 (ICN)");
const arrOrigin = createAirportField($("#arr-origin"), { scope: "all", name: "origin", placeholder: "예: 도쿄, NRT" });
const planAirport = createAirportField($("#plan-airport"), { scope: "origin", name: "airport", placeholder: "예: 김포 (GMP)" });
planAirport.set("", "");

$("#flt-no").addEventListener("input", (e) => {
  const pos = e.target.selectionStart;
  e.target.value = e.target.value.toUpperCase();
  e.target.setSelectionRange(pos, pos);
});

// ---------------- 지도 (구글 지도 임베드, API 키 불필요) ----------------
// 출발·도착 시간표는 출발지(위)·도착지(아래) 지도를 함께 띄운다. 편명 조회만 전환하며 기본은 도착지.
const MAP_DEFAULT = { dep: "to", arr: "to", flt: "to" };
const mapState = {};

function airportQuery(point) {
  if (!point || !(point.name || point.code)) return "";
  if (KR.includes(point.code)) return `${point.name}국제공항`.replace("국제공항국제공항", "국제공항");
  return point.name ? `${point.name} 공항` : `${point.code} airport`;
}

function setFrame(frame, q) {
  const src = `https://www.google.com/maps?q=${encodeURIComponent(q)}&output=embed&hl=ko&z=11`;
  if (frame.dataset.src !== src) {
    frame.dataset.src = src;
    frame.src = src;
  }
}

function drawMap(prefix) {
  const st = mapState[prefix];
  const card = $(`#${prefix}-map`);
  if (!st || !st.flight) return;
  if (card.classList.contains("map-dual")) {
    card.querySelectorAll(".map-pane").forEach((pane) => {
      const side = pane.dataset.side;
      const point = side === "from" ? st.flight.from : st.flight.to;
      const q = airportQuery(point);
      pane.hidden = !q;
      if (!q) return;
      pane.querySelector(".map-title").textContent = `${side === "from" ? "출발지" : "도착지"} · ${point.name || point.code}${point.code ? ` (${point.code})` : ""}`;
      setFrame(pane.querySelector("iframe"), q);
    });
    card.hidden = false;
    return;
  }
  const point = st.side === "from" ? st.flight.from : st.flight.to;
  const q = airportQuery(point);
  if (!q) return;
  card.querySelector(".map-title").textContent = `${st.side === "from" ? "출발지" : "도착지"} · ${point.name || point.code}${point.code ? ` (${point.code})` : ""}`;
  const src = `https://www.google.com/maps?q=${encodeURIComponent(q)}&output=embed&hl=ko&z=11`;
  const frame = card.querySelector("iframe");
  if (frame.dataset.src !== src) {
    frame.dataset.src = src;
    frame.src = src;
  }
  card.querySelectorAll(".map-seg .seg-btn").forEach((b) => b.classList.toggle("on", b.dataset.side === st.side));
  // 편명 조회: 지도에서 보고 있는 쪽(출발지/도착지) 공항 코드를 크게
  const list = $(`#${prefix}-list`);
  list.classList.toggle("focus-from", st.side === "from");
  list.classList.toggle("focus-to", st.side === "to");
  card.hidden = false;
}

function setMapFlight(prefix, from, to) {
  mapState[prefix] = { side: (mapState[prefix] && mapState[prefix].side) || MAP_DEFAULT[prefix], flight: { from, to } };
  drawMap(prefix);
}

Object.keys(MAP_DEFAULT).forEach((prefix) => {
  $(`#${prefix}-map`).querySelectorAll(".map-seg .seg-btn").forEach((b) => b.addEventListener("click", () => {
    mapState[prefix] = { ...(mapState[prefix] || {}), side: b.dataset.side };
    drawMap(prefix);
  }));
});

// ---------------- 보딩패스 카드 ----------------
function toneOf(remark) {
  if (!remark) return "info";
  if (/결항|cancel/i.test(remark)) return "bad";
  if (/지연|delay/i.test(remark)) return "warn";
  if (/탑승|boarding|마감|final/i.test(remark)) return "ok";
  if (/도착|출발|착륙|이륙|landed|departed/i.test(remark)) return "muted";
  return "info";
}

function stop(point, cls) {
  const s = el("div", null, `stop ${cls}`);
  s.append(el("b", point.code || "-"), el("small", point.name || ""));
  return s;
}

// 시각 한 줄: [출발] 08:50  (예상 시각이 다르면 아래에 표시)
function timeLine(label, scheduled, estimated) {
  const box = el("div", null, "tt");
  const row = el("div", null, "tt-row");
  row.append(el("span", label, "tt-lbl"), el("span", scheduled ? hhmm(scheduled) : "정보 없음", `tt-time${scheduled ? "" : " none"}`));
  if (!scheduled) row.title = "해외 공항의 시각은 제공되지 않습니다";
  box.appendChild(row);
  if (scheduled && estimated && estimated !== scheduled) {
    box.appendChild(el("div", `예상 ${hhmm(estimated)}`, `est ${estimated > scheduled ? "late" : "early"}`));
  }
  return box;
}

// 편명 조회: 출발·도착 한쪽의 날짜·시각·터미널·탑승구
function legBlock(label, leg, align) {
  const box = el("div", null, `leg ${align}`);
  box.appendChild(el("div", label, "leg-lbl"));
  if (!leg || !leg.scheduled) {
    box.appendChild(el("div", "정보 없음", "leg-time none"));
    box.appendChild(el("div", "해외 공항 시각은 국내 공공데이터에 없습니다", "leg-meta"));
    return box;
  }
  box.appendChild(el("div", hhmm(leg.scheduled), "leg-time"));
  box.appendChild(el("div", dateOf(leg.scheduled), "leg-date"));
  if (leg.estimated && leg.estimated !== leg.scheduled) {
    box.appendChild(el("div", `예상 ${hhmm(leg.estimated)}`, `est ${leg.estimated > leg.scheduled ? "late" : "early"}`));
  }
  const meta = [leg.terminal && `터미널 ${leg.terminal}`, `탑승구 ${leg.gate || "-"}`].filter(Boolean).join(" · ");
  box.appendChild(el("div", meta, "leg-meta"));
  return box;
}

function buildTicket(f, ours, both = false) {
  const dep = f.direction !== "arrival";
  const other = dep
    ? { code: f.destination_code, name: f.destination_name }
    : { code: f.origin_code, name: f.origin_name };
  const from = dep ? ours : other;
  const to = dep ? other : ours;

  const card = el("article", null, `ticket ${dep ? "dep" : "arr"}`);
  card.tabIndex = 0;
  const main = el("div", null, "ticket-main");
  const head = el("div", null, "ticket-head");
  head.append(
    el("span", f.airline || "-", "airline"),
    el("span", f.flight_no || "-", "fno"),
    el("span", dep ? "출발" : "도착", `dir ${dep ? "dep" : "arr"}`),
    el("span", f.remark || "예정", `status ${toneOf(f.remark)}`),
  );
  if (f.codeshares && f.codeshares.length) {
    const cs = el("span", `공동운항 ${f.codeshares.join(" · ")}`, "cs");
    cs.title = "같은 비행기를 여러 항공사 편명으로 판매합니다";
    head.insertBefore(cs, head.lastChild);
  }
  const route = el("div", null, "route");
  const track = el("div", null, "track");
  track.appendChild(el("span", "✈", "plane"));
  route.append(stop(from, "from"), track, stop(to, "to"));
  main.append(head, route);

  const side = el("div", null, "ticket-side");
  if (both) {
    // 편명 조회: 출발·도착 각각의 날짜·시각·탑승구를 카드 안에 크게 보여 준다
    const legs = f.legs || {
      departure: dep ? { scheduled: f.scheduled, estimated: f.estimated, terminal: f.terminal, gate: f.gate } : null,
      arrival: dep ? null : { scheduled: f.scheduled, estimated: f.estimated, terminal: f.terminal, gate: f.gate },
    };
    const row = el("div", null, "legs");
    row.append(legBlock("출발", legs.departure, "left"), legBlock("도착", legs.arrival, "right"));
    main.appendChild(row);
    card.classList.add("full");
    card.append(main);
    return { card, other, from, to };
  } else {
    side.appendChild(el("div", dep ? "출발 시각" : "도착 시각", "time-lbl"));
    side.appendChild(el("div", hhmm(f.scheduled), "time-big"));
    side.appendChild(el("div", dateOf(f.scheduled), "date-small"));
    if (f.estimated && f.estimated !== f.scheduled) {
      side.appendChild(el("div", `예상 ${hhmm(f.estimated)}`, `est ${f.estimated > f.scheduled ? "late" : "early"}`));
    }
  }
  const where = [f.terminal && `터미널 ${f.terminal}`, f.gate && `탑승구 ${f.gate}`].filter(Boolean).join(" · ");
  if (where) side.appendChild(el("div", where, "meta"));
  card.append(main, side);
  return { card, other, from, to };
}

function renderBoard(prefix, data) {
  const sum = $(`#${prefix}-summary`);
  const list = $(`#${prefix}-list`);
  const mapCard = $(`#${prefix}-map`);
  sum.replaceChildren();
  list.replaceChildren();
  mapCard.hidden = true;
  [
    data.searched ? null : `${data.airport_name} (${data.airport})`,
    fmtYmd(data.date),
    data.flight_no ? `편명 ${data.flight_no}` : null,
    `${data.count}건`,
    data.provider ? `자료 ${data.provider}` : null,
  ].filter(Boolean).forEach((text) => sum.appendChild(el("span", text, "chip")));
  if (data.note) list.appendChild(el("p", data.note, "notice"));
  if (!data.flights.length) return;

  let activeCard = null;
  const cards = data.flights.map((f, i) => {
    const code = f.airport || data.airport;
    const { card, from, to } = buildTicket(f, { code, name: NAME_OF[code] || data.airport_name }, prefix === "flt");
    const select = () => {
      if (activeCard) activeCard.classList.remove("on");
      activeCard = card;
      card.classList.add("on");
      setMapFlight(prefix, from, to);
    };
    card.addEventListener("click", select);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter") select();
    });
    if (i === 0) select();
    return card;
  });
  // 미리 보이는 개수: 지금 화면에 들어가는 카드 수 + 1 (최소 6). ⋮ 는 항상 화면 아래쪽 밖에서 시작한다.
  list.appendChild(cards[0]);
  const cardH = (cards[0].offsetHeight || 80) + 8;
  const room = window.innerHeight - list.getBoundingClientRect().top;
  const PREVIEW = Math.max(6, Math.ceil(room / cardH) + 1);
  cards.slice(1, PREVIEW).forEach((c) => list.appendChild(c));
  if (cards.length <= PREVIEW + 1) {
    cards.slice(PREVIEW).forEach((c) => list.appendChild(c));
    return;
  }
  const rest = cards.slice(PREVIEW);
  const more = el("button", null, "more-dots");
  more.type = "button";
  more.append(el("span", "⋮", "dots-v"), el("span", `내리면 ${rest.length}편 더 보기`, "dots-text"));
  list.appendChild(more);
  let opened = false;
  const startY = window.scrollY;
  const open = () => {
    if (opened) return;
    opened = true;
    window.removeEventListener("scroll", onScroll);
    flyover();
    setTimeout(() => {
      more.remove();
      rest.forEach((c, i) => {
        c.classList.add("reveal");
        c.style.animationDelay = `${Math.min(i, 14) * 40}ms`;
        list.appendChild(c);
      });
    }, 260);
  };
  // 사용자가 직접 아래로 내려 ⋮ 가 화면 아래 1/4 안으로 들어왔을 때만 연다
  function onScroll() {
    if (!more.isConnected) { window.removeEventListener("scroll", onScroll); return; }
    if (window.scrollY - startY < 60) return;
    if (more.getBoundingClientRect().top < window.innerHeight * 0.85) open();
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  more.addEventListener("click", open);
}

// 화면 전체를 반투명 비행기가 아래에서 위로 지나간다
function flyover() {
  const fx = $("#flyover");
  if (!fx || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches)) return;
  fx.classList.remove("go");
  void fx.offsetWidth;
  fx.classList.add("go");
}

function showError(prefix, msg) {
  $(`#${prefix}-summary`).replaceChildren();
  $(`#${prefix}-map`).hidden = true;
  $(`#${prefix}-list`).replaceChildren(el("p", msg, "notice error"));
}

async function runBoard(prefix, path, params) {
  $(`#${prefix}-summary`).replaceChildren();
  $(`#${prefix}-map`).hidden = true;
  $(`#${prefix}-list`).replaceChildren(el("p", "조회 중입니다...", "notice"));
  const body = await api(`${path}?${new URLSearchParams(params).toString()}`);
  if (!body.ok) {
    showError(prefix, body.error.message);
    return;
  }
  renderBoard(prefix, body.data);
}

$("#dep-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  let origin;
  let dest;
  try {
    origin = await depOrigin.resolve();
    if (!origin) throw new Error("출발 공항을 입력하십시오.");
    dest = await depDest.resolve();
  } catch (e) {
    showError("dep", e.message);
    return;
  }
  const params = { airport: origin, date: ymdOf($("#dep-date")), from_time: $("#dep-hour").value };
  if (dest) params.destination = dest;
  if (dest) track("route", origin, dest); else track("airport", origin);
  runBoard("dep", "/departures", params);
});

$("#arr-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  let airport;
  let origin;
  try {
    airport = await arrAirport.resolve();
    if (!airport) throw new Error("도착 공항을 입력하십시오.");
    origin = await arrOrigin.resolve();
  } catch (e) {
    showError("arr", e.message);
    return;
  }
  const params = { airport, date: ymdOf($("#arr-date")), from_time: $("#arr-hour").value };
  if (origin) params.origin = origin;
  if (origin) track("route", origin, airport); else track("airport", airport);
  runBoard("arr", "/arrivals", params);
});

$("#flt-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const flightNo = $("#flt-no").value.toUpperCase().replace(/\s+/g, "");
  if (!flightNo) {
    showError("flt", "편명을 입력하십시오 (예: KE1041).");
    return;
  }
  track("flight", flightNo);
  runBoard("flt", "/flight-search", { flight_no: flightNo, date: ymdOf($("#flt-date")) });
});

// ---------------- 실행 과정 (F3019) ----------------
function traceItems(trace) {
  const items = [];
  const lastArgs = {};
  (trace || []).forEach((e) => {
    const p = e.payload || {};
    const time = (e.ts || "").slice(11, 19);
    const add = (cls, title, detail) => items.push({ cls, title, detail, time });
    switch (e.event) {
      case "request_received":
        add("info", "요청 접수", p.message || `${p.chars}자`);
        break;
      case "intent_analyzed": {
        const cond = Object.entries(p.conditions || {}).map(([k, v]) => `${k}=${v}`).join(", ");
        add(p.source === "llm" ? "llm" : "rule",
          `의도 분석 (${p.source === "llm" ? "LLM" : "규칙"}) → ${INTENT_LABEL[p.intent] || p.intent}`,
          [p.reason && `이유: ${p.reason}`, cond && `조건: ${cond}`,
            p.inherited && p.inherited.length ? `이전 대화에서 이어받음: ${p.inherited.join(", ")}` : null, p.note]
            .filter(Boolean).join("\n"));
        break;
      }
      case "planned": {
        const steps = (p.steps || []).map((s) => TOOL_LABEL[s.tool] || s.tool);
        add("plan", `${p.attempt ? `재계획 ${p.attempt}차 ` : ""}계획: ${steps.length ? steps.join(" → ") : "도구 없이 답변"}`,
          [(p.steps || []).map((s) => `${TOOL_LABEL[s.tool] || s.tool}(${fmtArgs(s.args)})`).join("\n"),
            p.missing ? `되물음: ${p.missing}` : null,
            ...(p.attempt ? [] : (p.assumptions || []).map((a) => `가정: ${a}`))].filter(Boolean).join("\n"));
        break;
      }
      case "tool_called":
        lastArgs[p.tool] = p.args;
        break;
      case "tool_result":
        add(p.ok ? "ok" : "fail",
          `${p.ok ? "✓" : "✕"} ${TOOL_LABEL[p.tool] || p.tool}${p.ok ? (p.count != null ? ` ${p.count}건` : "") : ` 실패(${p.code})`} · ${p.elapsed_ms}ms`,
          fmtArgs(lastArgs[p.tool]));
        break;
      case "tool_reused":
        add("reuse", `↺ ${TOOL_LABEL[p.tool] || p.tool} 이미 받은 결과 재사용`, fmtArgs(p.args));
        break;
      case "replanned":
        add("replan", `재계획 ${p.attempt}회`, p.reason);
        break;
      case "judged": {
        const parts = [p.decision === "enrich" ? "상세 보강 후 응답" : "응답"];
        if (p.candidates != null) parts.push(`후보 ${p.candidates}편`);
        if (p.rejected) parts.push(`제외 ${p.rejected}편`);
        if (p.chosen) parts.push(`선택 ${p.chosen}`);
        add("judge", `판단: ${parts.join(" · ")}`, [(p.criteria || []).join(" · "), p.reason].filter(Boolean).join("\n"));
        break;
      }
      case "detail_skipped":
        add("info", `상세 조회 생략 (${p.flight_no})`, p.reason);
        break;
      case "responded":
        add("ok", `응답 생성 (${p.chars}자${p.cards ? `, 항공편 카드 ${p.cards}개` : ""})`, "");
        break;
      case "audit_referenced":
        add("info", `직전 실행 점검 결과 참조: ${AUDIT_LABEL[p.status] || p.status}`,
          (p.warnings || []).length ? `경고: ${p.warnings.map((c) => AUDIT_CODE[c] || c).join(", ")}` : "");
        break;
      case "memory_updated":
        add("info", "대화 맥락 저장", p.summary || "");
        break;
      case "engine_fallback":
        add("fail", "LangGraph 없이 실행", p.reason);
        break;
      case "cancelled":
        add("fail", "실행 멈춤", p.reason);
        break;
      case "agent_error":
        add("fail", "처리 중 오류", p.error);
        break;
      case "screen_query":
        add(p.ok ? "ok" : "fail",
          `${p.ok ? "✓" : "✕"} 화면 조회: ${TOOL_LABEL[p.tool] || p.tool}${p.ok ? (p.count != null ? ` ${p.count}건` : "") : ` 실패(${p.code})`} · ${p.elapsed_ms}ms`,
          fmtArgs(p.args));
        break;
      default:
        break;
    }
  });
  return items;
}

function renderSteps(bar, trace) {
  bar.replaceChildren();
  (trace || []).forEach((e) => {
    const p = e.payload || {};
    if (e.event === "step_finished") {
      if (bar.children.length) bar.appendChild(el("span", "→", "step-arrow"));
      const ms = p.elapsed_ms || 0;
      bar.appendChild(el("span", `${STEP_LABEL[p.step] || p.step} ${ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`}`, "step-chip"));
    } else if (e.event === "replanned") {
      if (bar.children.length) bar.appendChild(el("span", "→", "step-arrow"));
      bar.appendChild(el("span", `재계획 ${p.attempt}`, "step-chip replan"));
    }
  });
}

// ---------------- 이상 징후 판정 (로깅 Agent, F3020) ----------------
const AUDIT_LABEL = { normal: "이상 없음", anomaly: "이상", undetermined: "판정 불가" };
const AUDIT_CODE = {
  REPEATED_CALL: "같은 도구 같은 입력 반복 호출", EXCESSIVE_REPLAN: "재계획 과다 반복", UNPLANNED_CALL: "계획에 없던 도구 호출",
};
const AUDIT_CLASS = { normal: "ok", anomaly: "bad", undetermined: "warn" };

function auditTitle(a) {
  return a.status === "anomaly" ? `이상 ${a.warnings.length}건` : AUDIT_LABEL[a.status] || a.status;
}

// 실행 과정 맨 아래에 로깅 Agent 의 판정을 한 줄 덧붙인다
function appendAuditItem(list, a) {
  if (!a) return;
  const li = el("li", null, `tl ${a.status === "anomaly" ? "fail" : a.status === "undetermined" ? "replan" : "ok"}`);
  const head = el("div", null, "tl-head");
  head.append(el("span", `로깅 Agent 점검: ${auditTitle(a)}`, "tl-title"), el("span", "", "tl-time"));
  li.appendChild(head);
  const lines = (a.warnings || []).map((w) => `· ${w.label}: ${w.message}`);
  if (lines.length) li.appendChild(el("div", lines.join("\n"), "tl-detail"));
  list.appendChild(li);
}

// 원인 진단(LLM)은 판정과 따로 보여 준다
function diagnosisBox(d) {
  const box = el("div", null, "audit-diag");
  box.appendChild(el("div", "원인 진단 (LLM · 판정과 별도)", "audit-diag-title"));
  if (!d) {
    box.appendChild(el("div", "이상 판정이 아니어서 진단하지 않았습니다.", "audit-diag-text muted"));
  } else if (d.status === "done") {
    box.appendChild(el("div", d.text, "audit-diag-text"));
  } else if (d.status === "pending") {
    box.appendChild(el("div", "진단 중입니다. 잠시 후 다시 열면 결과가 보입니다.", "audit-diag-text muted"));
  } else {
    box.appendChild(el("div", d.reason || "진단하지 못했습니다.", "audit-diag-text muted"));
  }
  return box;
}

function renderAudit(host, data) {
  host.replaceChildren();
  if (!data || !data.runs || !data.runs.length) return;
  const head = el("div", null, "audit-head");
  const s = data.summary || {};
  head.append(el("span", "이상 징후 판정", "audit-head-title"),
    el("span", `이상 없음 ${s.normal || 0} · 이상 ${s.anomaly || 0} · 판정 불가 ${s.undetermined || 0}`, "audit-head-sum"));
  host.appendChild(head);
  data.runs.forEach((a) => {
    const card = el("div", null, `audit-card ${a.status}`);
    const top = el("div", null, "audit-top");
    top.append(el("span", `질문 ${a.run_index + 1}`, "audit-no"), el("span", a.question || "(질문 없음)", "audit-q"),
      el("span", auditTitle(a), `badge ${AUDIT_CLASS[a.status] || ""}`));
    card.appendChild(top);
    (a.warnings || []).forEach((w) => {
      const row = el("div", null, "audit-warn");
      row.append(el("b", w.label), el("span", ` ${w.message}`));
      const ev = (w.evidence || []).map((e) => `#${e.seq} ${e.event}${e.ts ? ` (${String(e.ts).slice(11, 19)})` : ""}`).join(", ");
      if (ev) row.appendChild(el("div", `근거 이벤트: ${ev}`, "audit-ev"));
      card.appendChild(row);
    });
    (a.undetermined || []).forEach((t) => card.appendChild(el("div", t, "audit-ev")));
    if (a.status === "anomaly") card.appendChild(diagnosisBox(a.diagnosis));
    host.appendChild(card);
  });
}

function renderTimeline(list, trace) {
  list.replaceChildren();
  const items = traceItems(trace);
  if (!items.length) {
    list.appendChild(el("li", "기록된 단계가 없습니다.", "tl-empty"));
    return;
  }
  items.forEach((it) => {
    const li = el("li", null, `tl ${it.cls}`);
    const head = el("div", null, "tl-head");
    head.append(el("span", it.title, "tl-title"), el("span", it.time, "tl-time"));
    li.appendChild(head);
    if (it.detail) li.appendChild(el("div", it.detail, "tl-detail"));
    list.appendChild(li);
  });
}

// ---------------- 사용 기록 (이 브라우저의 localStorage 에만 저장, 서버로 보내지 않음) ----------------
const USAGE_KEY = "planner-usage-v1";
// 추천 질문·표시에 쓰는 공항의 정식 한국어 명칭 (서버 airports.NAMES 와 같은 값)
const NAME_OF = {
  ICN: "인천국제공항", GMP: "김포국제공항", PUS: "김해국제공항", CJU: "제주국제공항", TAE: "대구국제공항",
  CJJ: "청주국제공항", KWJ: "광주공항", USN: "울산공항", HIN: "사천공항", RSU: "여수공항", WJU: "원주공항",
  YNY: "양양국제공항", KPO: "포항경주공항", KUV: "군산공항", MWX: "무안국제공항",
};

function loadUsage() {
  try {
    return JSON.parse(localStorage.getItem(USAGE_KEY)) || { airports: {}, routes: {}, places: {}, flights: [], facility: 0 };
  } catch (e) {
    return { airports: {}, routes: {}, places: {}, flights: [], facility: 0 };
  }
}

function track(kind, a, b, label) {
  const u = loadUsage();
  if (kind === "airport" && a) u.airports[a] = (u.airports[a] || 0) + 1;
  if (kind === "route" && a && b) {
    u.routes[`${a}-${b}`] = (u.routes[`${a}-${b}`] || 0) + 1;
    u.airports[a] = (u.airports[a] || 0) + 1;
    if (label) u.places[b] = label;
  }
  if (kind === "flight" && a) u.flights = [a, ...u.flights.filter((x) => x !== a)].slice(0, 5);
  if (kind === "facility") u.facility = (u.facility || 0) + 1;
  u.updated = Date.now();
  try { localStorage.setItem(USAGE_KEY, JSON.stringify(u)); } catch (e) { /* 저장 불가 시 무시 */ }
}

function topKeys(obj, n) {
  return Object.entries(obj || {}).sort((x, y) => y[1] - x[1]).slice(0, n).map(([k]) => k);
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// 추천 질문에는 공항 코드(NRT 등) 대신 한국어 이름만 쓴다. 한국어 이름을 모르면 null.
function placeName(code, u) {
  const name = NAME_OF[code] || (u.places && u.places[code]) || "";
  return /[가-힣]/.test(name) ? name.replace(/[()/]+/g, " ").replace(/\s+/g, " ").trim() : null;
}

// 사용 기록에 있는 해외 공항 코드의 한국어 이름을 서버 공항 목록에서 찾아 기록에 넣어 둔다
async function resolvePlaceNames() {
  const u = loadUsage();
  const codes = new Set(Object.keys(u.airports || {}));
  Object.keys(u.routes || {}).forEach((r) => r.split("-").forEach((c) => codes.add(c)));
  const need = [...codes].filter((c) => /^[A-Z]{3}$/.test(c) && !placeName(c, u));
  let changed = false;
  for (const c of need) {
    const body = await api(`/airports?q=${encodeURIComponent(c)}&scope=all`);
    const hit = body.ok ? body.data.find((a) => a.code === c) : null;
    if (hit && /[가-힣]/.test(hit.name)) {
      u.places = u.places || {};
      u.places[c] = hit.name;
      changed = true;
    }
  }
  if (changed) {
    try { localStorage.setItem(USAGE_KEY, JSON.stringify(u)); } catch (e) { /* 무시 */ }
  }
  return changed;
}

// 인천과 제주를 뺀 국내선을 잇는 노선은 환승객 전용 편이라 일반 승객 추천 질문에 쓰지 않는다
function transferOnly(a, b) {
  const domestic = (c) => Boolean(NAME_OF[c]) && c !== "ICN";
  return (a === "ICN" && domestic(b) && b !== "CJU") || (b === "ICN" && domestic(a) && a !== "CJU");
}

// 사용 기록이 있으면 그 공항·노선·편명으로, 없으면 김해–제주 노선으로 질문을 만든다. 들어올 때마다 순서가 바뀐다.
function suggestions() {
  const u = loadUsage();
  const pool = [];
  const routes = topKeys(u.routes, 3);
  const airportsUsed = topKeys(u.airports, 3);
  routes.forEach((r) => {
    const [a, b] = r.split("-");
    if (transferOnly(a, b)) {
      // 인천↔부산·대구 등은 환승객만 탈 수 있어 추천하지 않는다. 대신 김포 출발 질문으로 바꾼다.
      const other = a === "ICN" ? b : a;
      if (other !== "GMP" && NAME_OF[other]) pool.push(`${NAME_OF.GMP}에서 ${NAME_OF[other]} 가는 가장 빠른 항공편`);
      return;
    }
    const A = placeName(a, u);
    const B = placeName(b, u);
    // 출발·도착 조회는 국내 공항에서만 되므로 앞쪽은 국내 공항, 한국어 이름을 모르는 공항은 넣지 않는다
    if (!NAME_OF[a] || !A || !B) return;
    pool.push(`${A}에서 ${B} 가는 가장 빠른 항공편`, `내일 ${A}에서 ${B} 오후 3시 이후`,
      `${A}에 8시 도착하면 탈 수 있는 ${B}행 항공편`, `${A}에 도착하는 ${B}발 항공편`);
  });
  airportsUsed.forEach((a) => {
    const A = placeName(a, u);
    if (!NAME_OF[a] || !A) return;   // 혼잡도·시간표는 국내 공항만
    pool.push(`${A} 지금 붐벼?`, `${A} 오늘 출발 시간표 보여줘`, `${A} 몇 시에 제일 붐벼?`);
  });
  (u.flights || []).slice(0, 2).forEach((f) => pool.push(`${f} 지금 상태 알려줘`));
  if ((u.facility || 0) > 0 || airportsUsed.includes("ICN")) {
    pool.push("인천공항 출국장 약국 어디 있어?", "인천공항 면세점 어디 있어?");
  }
  if (!pool.length) {
    pool.push("김해국제공항에서 제주국제공항 가는 가장 빠른 항공편", "내일 김해국제공항에서 제주국제공항 오후 3시 이후",
      "김해국제공항에 7시 도착하면 탈 수 있는 제주행 항공편", "제주국제공항 도착하는 김해국제공항발 항공편",
      "김해국제공항 지금 붐벼?", "내일 아침 제주국제공항에서 김해국제공항 가는 항공편", "김해국제공항 몇 시에 제일 붐벼?");
  }
  return shuffle([...new Set(pool)]);
}

// 추천 질문은 최대 두 줄(3~5개)만 보이게 한다
function renderChips() {
  // 한국어 이름을 아직 모르는 공항이 있으면 찾아 온 뒤 한 번 더 그린다
  resolvePlaceNames().then((changed) => { if (changed) renderChips(); }).catch(() => {});
  const box = $("#chat-chips");
  box.replaceChildren();
  const list = suggestions();
  // 입력칸 예시도 매번 바꾼다. 사용 기록이 없으면 김포–제주 예시
  const u = loadUsage();
  const used = Object.keys(u.routes || {}).length || Object.keys(u.airports || {}).length || (u.flights || []).length;
  $("#chat-input").placeholder = used && list.length
    ? `예: ${list[Math.floor(Math.random() * list.length)]}`
    : "예: 김포에서 제주로 가는 가장 빠른 편 알려줘";
  list.slice(0, 5).forEach((text) => box.appendChild(button(text, "chip-btn", () => sendChat(text))));
  requestAnimationFrame(() => {
    const chips = [...box.children];
    if (!chips.length) return;
    const lines = [...new Set(chips.map((c) => c.offsetTop))].sort((x, y) => x - y);
    const limit = lines[1] != null ? lines[1] : lines[0];
    chips.forEach((c, i) => { if (c.offsetTop > limit && i >= 3) c.remove(); });
    [...box.children].forEach((c) => { if (c.offsetTop > limit) c.remove(); });
  });
}

// ---------------- AI 플래너 (대화) ----------------
let sessionId = null;
try { sessionId = sessionStorage.getItem("planner-session"); } catch (e) { sessionId = null; }
let busy = false;
// 진행 중인 질문: '새 대화'를 누르면 이 질문을 멈추고, 늦게 온 답은 버린다
let chatGen = 0;
let chatRun = null;     // { runId, abort, timer }

function newId() {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return [...a].map((x) => x.toString(16).padStart(2, "0")).join("");
}

function stopChat() {
  chatGen += 1;                                 // 이 질문의 답이 늦게 와도 화면에 넣지 않는다
  if (!chatRun) return;
  clearInterval(chatRun.timer);
  chatRun.abort.abort();                        // 브라우저 쪽 요청을 끊고
  api("/chat/cancel", { method: "POST", body: JSON.stringify({ run_id: chatRun.runId }) });  // 서버 쪽 실행도 멈춘다
  chatRun = null;
  busy = false;
  $("#chat-send").disabled = false;
}

function scrollChat() {
  const log = $("#chat-log");
  log.scrollTop = log.scrollHeight;
}

function addBubble(text, who) {
  const b = el("div", text, `bubble ${who}`);
  $("#chat-log").appendChild(b);
  scrollChat();
  return b;
}

function welcome() {
  $("#chat-log").replaceChildren();
  addBubble("안녕하십니까. 어디로 가십니까? 목적지만 말하면 출발 공항과 날짜를 정해 가장 이른 편을 찾고, 공항에 몇 시까지 가야 하는지 알려 드립니다.", "bot");
}

function setBanner(text) {
  const b = $("#chat-banner");
  b.textContent = text || "";
  b.hidden = !text;
}

function bannerFromTrace(trace) {
  const noKey = (trace || []).some((e) => e.event === "tool_result" && e.payload && e.payload.code === "NO_KEY");
  const gms = (trace || []).find((e) => e.event === "intent_analyzed" && e.payload && e.payload.note);
  if (noKey) return "공공데이터 키(DATA_GO_KR_KEY)가 없어 조회할 수 없습니다. 프로젝트 폴더 .env 에 키를 넣고 서버를 다시 켜십시오.";
  if (gms) return `${gms.payload.note} (.env 의 GMS_API_KEY 를 확인하십시오)`;
  return "";
}

function addBotReply(data) {
  const wrap = el("div", null, "bot-wrap");
  wrap.appendChild(el("div", data.answer, "bubble bot"));
  const meta = el("div", null, "bubble-meta");
  meta.appendChild(el("span", data.intent_source === "llm" ? "LLM 분석" : "규칙 분석", `badge ${data.intent_source === "llm" ? "llm" : "rule"}`));
  if (data.intent) meta.appendChild(el("span", INTENT_LABEL[data.intent] || data.intent, "badge"));
  if (data.replans) meta.appendChild(el("span", `재계획 ${data.replans}회`, "badge replan"));
  if (data.audit) {
    const b = el("span", `점검: ${auditTitle(data.audit)}`, `badge ${AUDIT_CLASS[data.audit.status] || ""}`);
    b.title = "로깅 Agent 가 이 실행의 기록을 규칙으로 점검한 결과";
    meta.appendChild(b);
  }
  meta.appendChild(button("실행 과정 보기", "link-btn", () => {
    setTraceCollapsed(false);
    renderSteps($("#trace-steps"), data.trace);
    renderTimeline($("#trace-list"), data.trace);
    appendAuditItem($("#trace-list"), data.audit);
  }));
  wrap.appendChild(meta);

  const cards = data.cards;
  if (cards && cards.flights && cards.flights.length) {
    const box = el("div", null, "chat-cards");
    const ours = { code: cards.airport, name: cards.airport_name };
    cards.flights.forEach((f, i) => {
      const { card } = buildTicket(f, ours);
      card.classList.add("compact");
      if (i === 0 && f.flight_no === cards.chosen) {
        card.classList.add("on");
        card.querySelector(".ticket-head").prepend(el("span", "추천", "pick"));
      }
      if (f.direction !== "arrival") {
        card.querySelector(".ticket-side").appendChild(button("이 편으로 계획 저장", "btn-ghost save-plan", (e) => {
          e.stopPropagation();
          prefillPlan(f, cards);
        }));
      }
      box.appendChild(card);
    });
    wrap.appendChild(box);
  }
  $("#chat-log").appendChild(wrap);
  scrollChat();
}

async function sendChat(message) {
  if (busy || !message) return;
  busy = true;
  $("#chat-send").disabled = true;
  addBubble(message, "user");
  const pending = el("div", null, "bubble bot pending");
  const label = el("span", "질문을 읽는 중");
  pending.append(label, el("span", "", "dots"));
  $("#chat-log").appendChild(pending);
  scrollChat();
  const t0 = Date.now();
  const phases = ["질문을 읽는 중", "찾을 자료를 정하는 중", "운항 자료를 받는 중", "조건과 맞춰 보는 중"];
  const timer = setInterval(() => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    label.textContent = `${phases[Math.min(phases.length - 1, Math.floor(sec / 2))]} · ${sec}초`;
  }, 500);
  renderSteps($("#trace-steps"), []);
  $("#trace-list").replaceChildren(el("li", "처리 중입니다…", "tl-empty"));

  const gen = ++chatGen;
  const runId = newId();
  const abort = new AbortController();
  chatRun = { runId, abort, timer };
  const body = await api("/chat", {
    method: "POST", signal: abort.signal,
    body: JSON.stringify({ message, session_id: sessionId, run_id: runId }),
  });
  if (gen !== chatGen) return;                  // '새 대화'로 멈춘 질문의 답은 버린다
  chatRun = null;
  clearInterval(timer);
  pending.remove();
  if (body.ok && body.data.cancelled) {
    busy = false;
    $("#chat-send").disabled = false;
    return;
  }
  if (body.ok) {
    sessionId = body.data.session_id;
    try { sessionStorage.setItem("planner-session", sessionId); } catch (e) { /* 저장 불가 시 무시 */ }
    addBotReply(body.data);
    const c = body.data.cards;
    if (c && c.flights && c.flights.length) {
      const f = c.flights[0];
      if (f.direction === "arrival") track("route", f.origin_code, c.airport, f.origin_name);
      else track("route", c.airport, f.destination_code, f.destination_name);
    }
    renderSteps($("#trace-steps"), body.data.trace);
    renderTimeline($("#trace-list"), body.data.trace);
    appendAuditItem($("#trace-list"), body.data.audit);
    setBanner(bannerFromTrace(body.data.trace));
  } else {
    addBubble(body.error.message, "bot error");
    $("#trace-list").replaceChildren(el("li", body.error.message, "tl fail"));
  }
  busy = false;
  $("#chat-send").disabled = false;
  $("#chat-input").focus();
}

$("#chat-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  input.value = "";
  sendChat(message);
});

renderChips();
window.addEventListener("resize", () => { clearTimeout(window.__chipTimer); window.__chipTimer = setTimeout(renderChips, 250); });

$("#chat-new").addEventListener("click", async () => {
  const old = sessionId;
  stopChat();                                   // 답을 기다리던 질문이 있으면 멈춘다
  sessionId = null;
  try { sessionStorage.removeItem("planner-session"); } catch (e) { /* 무시 */ }
  welcome();
  setBanner("");
  renderSteps($("#trace-steps"), []);
  $("#trace-list").replaceChildren(el("li", "질문하면 여기에 실행 과정이 표시됩니다.", "tl-empty"));
  if (old) await api(`/chat/${encodeURIComponent(old)}`, { method: "DELETE" });
});

welcome();

const TRACE_KEY = "planner-trace-collapsed";
function setTraceCollapsed(on) {
  $(".chat-layout").classList.toggle("trace-collapsed", on);
  $("#trace-toggle").setAttribute("aria-expanded", String(!on));
  $("#trace-toggle").textContent = on ? "펼치기" : "접기";
  try { localStorage.setItem(TRACE_KEY, on ? "1" : "0"); } catch (e) { /* 무시 */ }
  fitTrace();
}
$("#trace-toggle").addEventListener("click", () => setTraceCollapsed(!$(".chat-layout").classList.contains("trace-collapsed")));
$("#trace-open").addEventListener("click", () => setTraceCollapsed(false));

// 실행 과정 패널이 대화 창보다 길어지지 않게: 대화 창 높이를 최대 높이로 쓴다 (짧으면 내용만큼)
function fitTrace() {
  const panel = $("#trace-panel");
  const chat = $(".chat-panel");
  if (!panel || !chat) return;
  const sideBySide = getComputedStyle($(".chat-layout")).gridTemplateColumns.split(" ").length > 1;
  panel.style.maxHeight = sideBySide ? `${chat.offsetHeight}px` : "";
}
if (window.ResizeObserver) new ResizeObserver(fitTrace).observe($(".chat-panel"));
window.addEventListener("resize", fitTrace);
try { if (localStorage.getItem(TRACE_KEY) === "1") setTraceCollapsed(true); } catch (e) { /* 무시 */ }
fitTrace();

// ---------------- 내 계획 (F3015 · F3016) ----------------
let editingId = null;
let planLine = "domestic";
let lineTouched = false;   // 사용자가 노선을 직접 고쳤는지 (아니면 편명 조회 결과를 따른다)
const LINE_NAME = { domestic: "국내선", international: "국제선" };

function pad(n) {
  return String(n).padStart(2, "0");
}
function fmtDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
// '2026-11-19 18:00' → '11-19 18:00'
function short(v) {
  return v && v.length >= 16 ? `${v.slice(5, 10)} ${v.slice(11, 16)}` : (v || "");
}

// 날짜 + 시각을 테두리 하나짜리 박스로 묶는다.
// 날짜는 [연]-[월]-[일], 시각은 [시]:[분] 칸에 숫자로 친다.
// 칸이 차면 다음 칸으로 넘어간다: 연 4자리 → 월, 월 2자리(또는 2~9) → 일, 일 2자리(또는 4~9) → 시, 시 2자리(또는 3~9) → 분.
// 시·분은 ↑↓ 키로 1씩 바꿀 수 있고, 빈 칸에서 백스페이스를 누르면 앞 칸으로 돌아간다.
function createDateTime(host) {
  host.replaceChildren();
  const num = (ph, label, max) => {
    const x = el("input");
    x.type = "text";
    x.inputMode = "numeric";
    x.maxLength = max;
    x.placeholder = ph;
    x.autocomplete = "off";
    x.setAttribute("aria-label", label);
    return x;
  };
  const yy = num("연", "연", 4);
  const mo = num("월", "월", 2);
  const dd = num("일", "일", 2);
  const hh = num("시", "시", 2);
  const mm = num("분", "분", 2);
  const dwrap = el("div", null, "dt-date");
  dwrap.append(yy, el("span", "-", "dt-colon"), mo, el("span", "-", "dt-colon"), dd);
  const wrap = el("div", null, "dt-time");
  wrap.append(hh, el("span", ":", "dt-colon"), mm);
  host.append(dwrap, wrap);

  const listeners = [];
  const fire = () => listeners.forEach((fn) => fn());
  const digitsOnly = (x) => { x.value = x.value.replace(/\D/g, ""); };
  const two = (x, max) => {
    if (x.value === "") return;
    x.value = pad(Math.min(Number(x.value), max));
  };
  const step = (x, max, d) => {
    const v = x.value === "" ? 0 : (Number(x.value) + d + max + 1) % (max + 1);
    x.value = pad(v);
    fire();
  };
  const go = (x) => { x.focus(); x.select(); };

  yy.addEventListener("input", () => {
    digitsOnly(yy);
    if (yy.value.length === 4) go(mo);
    fire();
  });
  mo.addEventListener("input", () => {
    digitsOnly(mo);
    if (mo.value.length === 2 || /^[2-9]$/.test(mo.value)) { two(mo, 12); go(dd); }
    fire();
  });
  dd.addEventListener("input", () => {
    digitsOnly(dd);
    if (dd.value.length === 2 || /^[4-9]$/.test(dd.value)) { two(dd, 31); go(hh); }
    fire();
  });
  hh.addEventListener("input", () => {
    digitsOnly(hh);
    // 두 자리를 쳤거나 3~9로 시작하면 바로 분 칸으로
    if (hh.value.length === 2 || /^[3-9]$/.test(hh.value)) { two(hh, 23); go(mm); }
    fire();
  });
  mm.addEventListener("input", () => {
    digitsOnly(mm);
    if (mm.value.length === 2) two(mm, 59);
    fire();
  });
  mo.addEventListener("blur", () => { two(mo, 12); fire(); });
  dd.addEventListener("blur", () => { two(dd, 31); fire(); });
  hh.addEventListener("blur", () => { two(hh, 23); fire(); });
  mm.addEventListener("blur", () => {
    if (mm.value === "" && hh.value !== "") mm.value = "00";
    two(mm, 59);
    fire();
  });
  hh.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); step(hh, 23, e.key === "ArrowUp" ? 1 : -1); }
    if ((e.key === ":" || e.key === "ArrowRight") && hh.selectionStart === hh.value.length) { e.preventDefault(); go(mm); }
    if (e.key === "Backspace" && hh.value === "") { e.preventDefault(); go(dd); }
  });
  mm.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); step(mm, 59, e.key === "ArrowUp" ? 1 : -1); }
    if ((e.key === "Backspace" || e.key === "ArrowLeft") && mm.selectionStart === 0 && mm.selectionEnd === 0) { e.preventDefault(); go(hh); }
  });
  dd.addEventListener("keydown", (e) => {
    if (e.key === "Backspace" && dd.value === "") { e.preventDefault(); go(mo); }
  });
  mo.addEventListener("keydown", (e) => {
    if (e.key === "Backspace" && mo.value === "") { e.preventDefault(); go(yy); }
  });
  [yy, mo, dd, hh, mm].forEach((x) => x.addEventListener("focus", () => x.select()));
  dwrap.addEventListener("mousedown", (e) => { if (e.target === dwrap) { e.preventDefault(); yy.focus(); } });
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) { e.preventDefault(); hh.focus(); } });

  const time = () => (hh.value.length === 2 && mm.value.length === 2 ? `${hh.value}:${mm.value}` : "");
  // 연·월·일이 모두 맞을 때만 YYYY-MM-DD 를 준다 (2월 30일 같은 날짜는 빈 값)
  const dateVal = () => {
    if (yy.value.length !== 4 || !mo.value || !dd.value) return "";
    const y = Number(yy.value);
    const m = Number(mo.value);
    const d = Number(dd.value);
    if (m < 1 || m > 12 || d < 1 || d > new Date(y, m, 0).getDate()) return "";
    return `${yy.value}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  };
  const setParts = (v) => {
    const [y = "", m = "", d = ""] = v ? v.split("-") : [];
    yy.value = y;
    mo.value = m;
    dd.value = d;
  };
  return {
    // 날짜와 시각이 모두 있을 때만 값을 준다
    get() { return dateVal() && time() ? `${dateVal()} ${time()}` : ""; },
    date() { return dateVal(); },
    hm() { return time(); },
    setDate(d) { setParts(d || ""); },
    // 날짜·시각 중 무엇이 비었는지: "" (다 있음 또는 다 비었음), "date", "time", "both"
    missing() {
      const t = Boolean(time());
      const dv = dateVal();
      if (dv && t) return "";
      const anyDate = yy.value || mo.value || dd.value;
      if (!anyDate && !t && !hh.value && !mm.value) return "both";
      return dv ? "time" : "date";
    },
    set(v) {
      setParts(v ? v.slice(0, 10) : "");
      hh.value = v ? v.slice(11, 13) : "";
      mm.value = v ? v.slice(14, 16) : "";
    },
    lock(on) {
      [yy, mo, dd, hh, mm].forEach((x) => { x.disabled = on; });
      host.classList.toggle("locked", on);
    },
    onChange(fn) { listeners.push(fn); },
  };
}
const planDep = createDateTime($("#plan-dep"));
const planArrive = createDateTime($("#plan-arrive"));
let depManual = false;
let depFor = "";           // 지금 항공기 출발 시간이 어느 편명으로 찾은 값인지     // 항공기 출발 시간을 사용자가 직접 고쳤는지 (저장 때 조회 결과보다 우선)
let depLocked = true;
function setDepLocked(on) {
  depLocked = on;
  planDep.lock(on);
  $("#plan-dep-unlock").textContent = on ? "직접 수정" : "잠그기";
}
$("#plan-dep-unlock").addEventListener("click", () => setDepLocked(!depLocked));
setDepLocked(true);

function setLine(line) {
  planLine = line || "";
  document.querySelectorAll("#plan-line .seg-btn").forEach((b) => {
    const on = b.dataset.line === planLine;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", String(on));
  });
}
document.querySelectorAll("#plan-line .seg-btn").forEach((b) => b.addEventListener("click", () => {
  lineTouched = true;
  setLine(b.dataset.line);
  if ($("#plan-msg").textContent) setPlanMsg("");
}));
setLine("domestic");   // 노선은 언제나 국내선이 먼저 선택되어 있다

// 오류는 '입력 지우기·저장' 버튼 바로 위에 작은 빨간 글씨로
function setPlanMsg(text, cls) {
  const p = $("#plan-msg");
  p.textContent = text || "";
  p.className = `msg plan-err ${cls || ""}`;
}

let planList = [];   // 저장된 계획 목록 (표 순서). 수정 제목의 번호는 여기서 매긴다

// 수정 중이면 현재 저장된 계획 중 몇 번째인지 1부터 번호를 붙인다
function renderEditTitle() {
  if (!editingId) { $("#plan-form-title").textContent = "계획 입력"; return; }
  const idx = planList.findIndex((p) => p.id === editingId);
  $("#plan-form-title").textContent = idx >= 0 ? `계획 수정 (#${idx + 1})` : "계획 수정";
}

function setEditing(id) {
  editingId = id;
  $("#plan-submit").textContent = id ? "수정 저장" : "저장";
  renderEditTitle();
}

function planPayload() {
  const f = $("#plan-form").elements;
  const arrive = planArrive.get();
  return {
    title: f.title.value.trim(),
    memo: f.memo.value.trim() || null,
    airport: f.airport.value,
    flight_no: f.flight_no.value.trim(),
    line: planLine,
    estimated: planDep.get() || null,
    dep_manual: depManual,
    buffer_min: f.buffer_min.value === "" ? null : Number(f.buffer_min.value),
    user_arrival: arrive || null,
  };
}

function fillPlanForm(p) {
  const f = $("#plan-form").elements;
  f.title.value = p.title || "";
  f.memo.value = p.memo || "";
  f.flight_no.value = p.flight_no || "";
  planDep.set(p.estimated || p.scheduled || "");
  planArrive.set(p.arrival_auto ? "" : (p.user_arrival || ""));
  f.buffer_min.value = p.buffer_min != null && p.buffer_min !== "" ? p.buffer_min : "";
  setLine(p.line || "domestic");
  lineTouched = Boolean(p.line);
  planAirport.set(p.airport, `${NAME_OF[p.airport] || p.airport_name || p.airport} (${p.airport})`);
  depManual = p.time_source === "manual";
  depFor = (p.flight_no || "").replace(/\s+/g, "").toUpperCase();
  setDepLocked(true);
  lookupFlight({ keep: true });
}

// AI 가 추천한 편을 계획 입력칸에 채운다 (저장은 사용자가 확인 후 누른다)
function prefillPlan(f, cards) {
  setEditing(null);
  fillPlanForm({
    title: `${f.destination_name || f.destination_code || "여행"} ${f.flight_no}`.trim(),
    airport: cards.airport, airport_name: cards.airport_name, flight_no: f.flight_no,
    estimated: f.estimated || f.scheduled,
    line: KR.includes(cards.airport) && KR.includes(f.destination_code) ? "domestic" : "international",
  });
  showTab("plans");
  setPlanMsg("");
}

// 편명·공항을 넣으면 출발 시각과 체크인 마감을 미리 조회한다
let lookupSeq = 0;
let lastLookup = null;

// 필수 항목 다섯 가지(제목, 공항, 편명, 노선, 내 공항 도착 예정)가 모두 채워졌는지
function requiredFilled() {
  const f = $("#plan-form").elements;
  return Boolean(f.title.value.trim() && f.airport.value && f.flight_no.value.trim() && planLine && planArrive.get());
}

// 비어 있는 필수 칸 이름 목록 (저장을 눌렀을 때 알려 준다)
function missingFields() {
  const f = $("#plan-form").elements;
  const out = [];
  if (!f.title.value.trim()) out.push({ name: "제목", el: f.title });
  if (!f.airport.value) out.push({ name: "출발 공항", el: $("#plan-airport input[type=text]") });
  if (!f.flight_no.value.trim()) out.push({ name: "편명", el: f.flight_no });
  if (!planLine) out.push({ name: "노선", el: $("#plan-line .seg-btn") });
  const m = planArrive.missing();
  if (m === "both") out.push({ name: "내 공항 도착 예정", el: $("#plan-arrive .dt-date input") });
  else if (m === "date") out.push({ name: "내 공항 도착 예정(날짜)", el: $("#plan-arrive .dt-date input") });
  else if (m === "time") out.push({ name: "내 공항 도착 예정(시·분)", el: $("#plan-arrive .dt-time input") });
  return out;
}

// 빨간 안내는 저장 버튼을 눌렀을 때만 검사해서 띄운다. 필수 칸 안내가 가장 먼저다.
function showMissing() {
  const miss = missingFields();
  if (miss.length) {
    setPlanMsg(`다음 항목을 입력하십시오: ${miss.map((x) => x.name).join(", ")}`, "error");
    return miss;
  }
  // 항공기 출발 시간을 직접 고치다 시·분이나 날짜만 비운 경우
  const dm = depLocked ? "" : planDep.missing();
  if (dm === "time" || dm === "date") {
    setPlanMsg(`항공기 출발 시간의 ${dm === "time" ? "시·분" : "날짜"}을 입력하십시오.`, "error");
    return [{ name: "항공기 출발 시간", el: $(`#plan-dep ${dm === "time" ? ".dt-time input" : ".dt-date input"}`) }];
  }
  return [];
}
// 안내가 떠 있을 때 사용자가 칸을 고치기 시작하면 안내를 내린다 (다시 검사는 저장을 눌렀을 때)
["input", "change"].forEach((t) => $("#plan-form").addEventListener(t, () => {
  if ($("#plan-msg").textContent) setPlanMsg("");
}));

// 편명 조회로 알아낸 노선과 고른 노선이 같은지. show 가 참일 때(저장을 눌렀을 때)만 안내를 띄운다.
function checkLine(show = false) {
  const bad = Boolean(lastLookup && lastLookup.found && lastLookup.destination && planLine && lastLookup.line !== planLine);
  if (bad && show) {
    setPlanMsg(`${lastLookup.flight_no} 편은 ${LINE_NAME[lastLookup.line]}입니다 (${lastLookup.airport}→${lastLookup.destination}). ${LINE_NAME[lastLookup.line]}으로 고치십시오.`, "error");
  }
  return !bad;
}

// "YYYY-MM-DD HH:MM" 문자열에 분을 더한다
// 도착 예정 시각 기준으로 출발 날짜를 정한다.
// 출발 시각(시:분)이 도착 시각보다 이르면 다음 날 편이다 (예: 23:30 도착 → 01:10 출발은 다음 날).
function alignDep(arrive, depHm) {
  const ad = arrive.slice(0, 10);
  const at = arrive.slice(11, 16);
  const next = shiftTime(`${ad} 00:00`, 1440).slice(0, 10);
  return `${at && depHm < at ? next : ad} ${depHm}`;
}

// 도착 예정을 고치면 항공기 출발 시간의 날짜를 맞춘다 (직접 고친 출발 시간은 건드리지 않는다)
// - 도착 날짜만 넣었으면: 출발 날짜를 같은 날로
// - 도착 날짜·시각을 넣었으면: 출발 시각이 도착 시각보다 이르면 다음 날로
// - 도착 시각만 넣고 날짜가 비었으면: 출발 날짜에서 거꾸로 도착 날짜를 채운다 (01:10 출발에 23:30 도착이면 전날)
function syncDates() {
  if (depManual) return;
  const depHm = planDep.hm();
  const depDate = planDep.date();
  const arrHm = planArrive.hm();
  if (!planArrive.date() && arrHm && depDate && depHm) {
    const prev = shiftTime(`${depDate} 00:00`, -1440).slice(0, 10);
    planArrive.setDate(arrHm > depHm ? prev : depDate);
  }
  const ad = planArrive.date();
  if (!ad) return;
  if (depHm) planDep.set(alignDep(planArrive.get() || ad, depHm));
  else planDep.setDate(ad);
}

function shiftTime(s, minutes) {
  const d = new Date(`${s.replace(" ", "T")}:00`);
  d.setMinutes(d.getMinutes() + minutes);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 편명·공항으로 출발 편을 찾는다. 도착 예정이 있으면 도착 뒤 24시간 안의 출발만 채택한다.
// 조회 결과의 체크인 마감. 공항 체크인 시간을 입력했으면 출발 − 입력값으로 다시 계산한다 (없으면 서버 기본값).
function checkinDeadlineFor(d, f) {
  const raw = f.buffer_min.value;
  const dep = d.estimated || d.scheduled;
  if (raw === "" || !Number.isFinite(Number(raw)) || !dep) return d.checkin_deadline;
  const t = new Date(dep.replace(" ", "T"));
  t.setMinutes(t.getMinutes() - Number(raw));
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
}
async function lookupFlight(opts = {}) {
  const f = $("#plan-form").elements;
  const hint = $("#plan-lookup") || document.createElement("p");   // 안내 줄이 화면에 없어도 조회는 한다
  const no = f.flight_no.value.replace(/\s+/g, "").toUpperCase();
  let airport = f.airport.value;
  const arrive = planArrive.get();      // 내 공항 도착 예정 ("" 이면 아직 없음)
  lastLookup = null;
  if (!no || no.length < 3) {
    hint.textContent = "편명과 공항을 넣으면 출발 시각과 체크인 마감을 찾아 채웁니다.";
    hint.className = "lookup-hint";
    if (!opts.keep && !depManual) planDep.set("");
    checkLine();
    return;
  }
  try { airport = airport || await planAirport.resolve(); } catch (e) { airport = ""; }
  if (!airport) return;
  const day = planArrive.date() || todayKst();                  // "YYYY-MM-DD"
  const days = arrive ? [day, shiftTime(`${day} 00:00`, 1440).slice(0, 10)] : [day];
  const mine = ++lookupSeq;
  hint.textContent = `${no} 출발 편을 찾는 중…`;
  hint.className = "lookup-hint";
  const inWindow = (t) => Boolean(t) && t >= arrive && t <= shiftTime(arrive, 1440);
  let first = null;
  let best = null;
  for (const dd of days) {
    const body = await api(`/plans/lookup?airport=${airport}&flight_no=${encodeURIComponent(no)}&date=${dd.replaceAll("-", "")}`);
    if (mine !== lookupSeq) return;
    if (!body.ok || !body.data.found) continue;
    const x = { ...body.data, flight_no: no, airport };
    first = first || x;
    if (arrive && inWindow(x.estimated || x.scheduled)) { best = x; break; }
  }
  const d = best || first;
  if (!d) {
    hint.textContent = `${fmtYmd(day.replaceAll("-", ""))} ${NAME_OF[airport] || airport} 출발 목록에 ${no} 편이 없습니다.`;
    hint.className = "lookup-hint warn";
    // 다른 편명으로 찾았던 출발 시간이면 지운다 (같은 편의 값은 그대로 둔다)
    if (!opts.keep && !depManual && depFor !== no) planDep.set("");
    checkLine();
    return;
  }
  lastLookup = d;
  if (arrive) {
    if (best) {
      planDep.set(best.estimated || best.scheduled);
      depManual = false;
      depFor = no;
    } else if (!depManual) {
      planDep.set(alignDep(arrive, (d.estimated || d.scheduled).slice(11, 16)));
      depFor = no;
    }
  }
  if (!lineTouched) setLine(d.line);
  let text = `${d.airline || ""} ${no} · ${hhmm(d.scheduled)} 출발${d.estimated && d.estimated !== d.scheduled ? ` (예상 ${hhmm(d.estimated)})` : ""}`
    + ` → ${d.destination_name || d.destination || "-"} · ${LINE_NAME[d.line]} 체크인 마감 ${hhmm(checkinDeadlineFor(d, f))}`;
  if (arrive && !best) text += " · 운항 시각을 도착 예정 날짜에 맞춰 넣었습니다.";
  hint.textContent = text;
  hint.className = best || !arrive ? "lookup-hint ok" : "lookup-hint warn";
  checkLine();
}
let lookupTimer = null;
$("#plan-form").elements.flight_no.addEventListener("input", () => {
  clearTimeout(lookupTimer);
  lookupTimer = setTimeout(lookupFlight, 500);
});
planDep.onChange(() => { depManual = true; });
planArrive.onChange(() => {
  syncDates();
  clearTimeout(lookupTimer);
  lookupTimer = setTimeout(lookupFlight, 300);
});
$("#plan-airport").addEventListener("focusout", () => setTimeout(lookupFlight, 200));

function minutesBetween(a, b) {
  const x = new Date(String(a).replace(" ", "T"));
  const y = new Date(String(b).replace(" ", "T"));
  const m = Math.round((x - y) / 60000);
  return Number.isFinite(m) ? m : null;
}

function iconButton(label, glyph, cls, onClick) {
  const b = button(glyph, `icon-btn ${cls}`, onClick);
  b.title = label;
  b.setAttribute("aria-label", label);
  return b;
}

function td(text, cls, title) {
  const c = el("td", text == null || text === "" ? "-" : text, cls);
  c.title = title || text || "";
  return c;
}

async function loadPlans() {
  const body = await api("/plans");
  const tbody = $("#plan-table tbody");
  tbody.replaceChildren();
  if (body.ok) { planList = body.data; renderEditTitle(); }
  if (!body.ok) {
    setPlanMsg(body.error.message, "error");
    return;
  }
  if (!body.data.length) {
    const tr = el("tr", null, "empty-tr");
    const c = el("td", "저장된 계획이 없습니다.", "empty-row");
    c.colSpan = 9;
    tr.appendChild(c);
    tbody.appendChild(tr);
    return;
  }
  body.data.forEach((p) => {
    const tr = el("tr");
    const dep = p.estimated || p.scheduled;
    const line = p.line ? (p.line === "international" ? "국제" : "국내") : "";
    const slack = p.checkin_deadline ? minutesBetween(p.checkin_deadline, p.user_arrival) : null;
    tr.append(
      td(p.title, "strong"),
      td(p.memo, "muted"),
      td(`${p.flight_no} ${p.airport}${p.destination ? `→${p.destination}` : ""}`),
      td(`${short(dep)}${p.time_source === "manual" ? "*" : ""}`, "",
        `${dep}${p.estimated ? " (예상)" : ""}${p.time_source === "manual" ? " · 직접 넣은 시각" : " · 운항 정보"}`),
      td(p.checkin_deadline ? `${hhmm(p.checkin_deadline)} ${line}` : "-", "",
        p.checkin_deadline ? `${p.checkin_deadline} · ${LINE_NAME[p.line] || ""} 출발 ${p.checkin_min}분 전` : ""),
      td(`${hhmm(p.user_arrival)}${p.arrival_auto ? " 자동" : ""}`, p.arrival_auto ? "auto" : "",
        `${p.user_arrival}${p.arrival_auto ? ` · 비워 둬서 체크인 마감으로 잡음` : ""}`),
      td(slack == null ? "-" : `${slack}분`, slack != null && slack < 15 ? "tight" : "", "체크인 마감까지 남는 시간"),
      td(short((p.created_at || "").replace("T", " ")), "muted"),
    );
    const act = el("td", null, "row-actions");
    act.append(
      iconButton("수정", "✎", "edit", () => {
        fillPlanForm(p);
        setEditing(p.id);
        setPlanMsg("");
        $("#plan-form").scrollIntoView({ behavior: "smooth", block: "start" });
      }),
      iconButton("삭제", "✕", "del", async () => {
        const r = await api(`/plans/${p.id}`, { method: "DELETE" });
        setPlanMsg(r.ok ? "" : r.error.message, r.ok ? "" : "error");
        if (editingId === p.id) resetPlanForm();
        loadPlans();
      }),
    );
    tr.appendChild(act);
    tbody.appendChild(tr);
  });
}

$("#plan-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  try { await planAirport.resolve(); } catch (e) { /* 아래 검사에서 막는다 */ }
  const miss = showMissing();
  if (miss.length) {
    // 비어 있는 칸을 글로 알리고 첫 빈칸으로 커서를 옮긴다
    if (miss[0].el) miss[0].el.focus();
    return;
  }
  const payload = planPayload();
  if (!checkLine(true) || planBusy) return;
  const path = editingId ? `/plans/${editingId}` : "/plans";
  setPlanBusy(true, editingId ? "수정한 계획을 저장하는 중입니다…" : "계획을 저장하는 중입니다…");
  let body;
  try {
    body = await api(path, { method: editingId ? "PUT" : "POST", body: JSON.stringify(payload) });
    if (body.ok) await loadPlans();   // 저장 목록에 반영될 때까지 기다린다
  } finally {
    setPlanBusy(false);
  }
  if (!body.ok) {
    setPlanMsg(body.error.message, "error");
    return;
  }
  const warns = body.data.warnings || [];
  resetPlanForm();
  setPlanMsg(warns.length ? `주의: ${warns.join(" ")}` : "", warns.length ? "warn" : "");
});

// 입력칸을 처음 상태로 되돌린다 (저장·삭제 뒤, 입력 지우기)
function resetPlanForm() {
  $("#plan-form").reset();
  planDep.set("");
  planArrive.set("");
  depManual = false;
  depFor = "";
  setDepLocked(true);
  lineTouched = false;
  setLine("domestic");
  planAirport.set("", "");
  setEditing(null);
  setPlanMsg("");
  lookupFlight();
}

// 저장 중에는 버튼을 막고 문구를 보인다
let planBusy = false;
function setPlanBusy(on, text) {
  planBusy = on;
  $("#plan-submit").disabled = on;
  const p = $("#plan-busy");
  p.textContent = on ? text : "";
  p.hidden = !on;
}

$("#plan-reset").addEventListener("click", resetPlanForm);

// ---------------- 실행 로그 (F3014) ----------------
let logShown = null;

async function loadLogs() {
  const body = await api("/logs");
  const list = $("#log-list");
  list.replaceChildren();
  if (!body.ok) {
    list.appendChild(el("li", body.error.message, "notice error"));
    return;
  }
  if (!body.data.length) {
    list.appendChild(el("li", "아직 기록이 없습니다. AI 플래너에 질문하거나 시간표를 조회하면 여기에 남습니다.", "notice"));
    return;
  }
  body.data.forEach((s) => {
    const li = el("li", null, `log-card${logShown === s.session_id ? " on" : ""}`);
    const info = el("div", null, "log-info");
    info.appendChild(el("div", s.title, "log-title"));
    const meta = el("div", null, "log-meta");
    meta.append(el("span", s.kind === "screen" ? "화면 조회" : "AI 대화", `badge ${s.kind === "screen" ? "" : "llm"}`),
      el("span", (s.started || "").replace("T", " ")),
      el("span", `도구 ${s.tool_calls}회`));
    if (s.replans) meta.appendChild(el("span", `재계획 ${s.replans}회`, "badge replan"));
    if (s.errors) meta.appendChild(el("span", `실패 ${s.errors}건`, "badge bad"));
    if (s.anomalies) meta.appendChild(el("span", `이상 징후 ${s.anomalies}`, "badge bad"));
    info.appendChild(meta);
    info.addEventListener("click", () => showLog(s.session_id, s.title));
    const del = button("삭제", "btn-danger", async () => {
      const r = await api(`/logs/${encodeURIComponent(s.session_id)}`, { method: "DELETE" });
      if (!r.ok) $("#log-detail-sub").textContent = r.error.message;
      if (logShown === s.session_id) clearLogDetail();
      loadLogs();
    });
    li.append(info, del);
    list.appendChild(li);
  });
}

function clearLogDetail() {
  logShown = null;
  $("#log-detail-title").textContent = "상세";
  $("#log-detail-sub").textContent = "왼쪽 목록에서 하나를 고르십시오.";
  $("#log-steps").replaceChildren();
  $("#log-audit").replaceChildren();
  $("#log-timeline").replaceChildren();
  $("#log-raw").hidden = true;
  $("#log-raw-toggle").hidden = true;
}

async function showLog(sid, title) {
  const body = await api(`/logs/${encodeURIComponent(sid)}`);
  if (!body.ok) {
    $("#log-timeline").replaceChildren(el("li", body.error.message, "tl fail"));
    return;
  }
  logShown = sid;
  $("#log-detail-title").textContent = title || sid;
  $("#log-detail-sub").textContent = `세션 ${sid} · 이벤트 ${body.data.events.length}개 (발생 순서)`;
  renderSteps($("#log-steps"), body.data.events);
  renderTimeline($("#log-timeline"), body.data.events);
  loadAudit(sid);
  const raw = $("#log-raw");
  raw.textContent = body.data.events.map((e) => `${e.ts}  ${e.event}  ${JSON.stringify(e.payload)}`).join("\n");
  raw.hidden = true;
  $("#log-raw-toggle").hidden = false;
  $("#log-raw-toggle").textContent = "원본 보기";
  loadLogs();
}

let auditTimer = null;
async function loadAudit(sid) {
  clearTimeout(auditTimer);
  const host = $("#log-audit");
  if (sid === "screen") { host.replaceChildren(); return; }   // 화면 조회 기록은 에이전트 실행이 아니라 판정하지 않는다
  const body = await api(`/logs/${encodeURIComponent(sid)}/audit`);
  if (logShown !== sid) return;
  if (!body.ok) { host.replaceChildren(el("div", body.error.message, "notice error")); return; }
  renderAudit(host, body.data);
  // 진단이 아직 진행 중이면 잠시 뒤 한 번 더 읽는다
  if (body.data.runs.some((r) => r.diagnosis && r.diagnosis.status === "pending")) {
    auditTimer = setTimeout(() => { if (logShown === sid) loadAudit(sid); }, 4000);
  }
}

$("#log-raw-toggle").addEventListener("click", () => {
  const raw = $("#log-raw");
  raw.hidden = !raw.hidden;
  $("#log-raw-toggle").textContent = raw.hidden ? "원본 보기" : "원본 닫기";
  if (!raw.hidden) raw.scrollIntoView({ behavior: "smooth", block: "nearest" });
});
$("#log-refresh").addEventListener("click", loadLogs);

// ---------------- 공항 시설 · 혼잡 (3D) ----------------
const FAC_AIRPORTS = [["ICN", "인천"], ["GMP", "김포"], ["PUS", "김해"], ["CJU", "제주"], ["TAE", "대구"], ["CJJ", "청주"]];
const FAC_OTHERS = [["KWJ", "광주"], ["USN", "울산"], ["HIN", "사천"], ["RSU", "여수"], ["WJU", "원주"], ["YNY", "양양"],
  ["KPO", "포항"], ["KUV", "군산"], ["MWX", "무안"]];
const CAT_COLOR = {
  "면세점": "#7c5cff", "식당·카페": "#f59e0b", "편의점·쇼핑": "#10b981", "약국·의료": "#ef4444",
  "환전·은행": "#0ea5e9", "라운지·휴식": "#a855f7", "편의시설": "#64748b", "기타": "#94a3b8",
};
const LEVEL_TEXT = { "원활": 1, "여유": 1, "보통": 2, "혼잡": 3, "붐빔": 3, "매우혼잡": 4, "정체": 4 };
const fac = { code: "ICN", data: null, mode: "departure", area: "", category: "", terminal: "", keyword: "", shown: 60 };

function levelOf(ratio) {
  if (ratio == null) return { text: "자료 없음", cls: "muted" };
  if (ratio >= 0.8) return { text: "붐빔", cls: "bad" };
  if (ratio >= 0.5) return { text: "보통", cls: "warn" };
  return { text: "여유", cls: "ok" };
}

function levelNumber(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  if (Number.isFinite(n)) return n;
  const key = String(v).replace(/\s/g, "");
  return LEVEL_TEXT[key] != null ? LEVEL_TEXT[key] : null;
}

function hourOf(v) {
  const digits = String(v == null ? "" : v).replace(/\D/g, "");
  if (!digits) return null;
  const h = digits.length >= 3 ? Number(digits.slice(0, digits.length - 2)) : Number(digits);
  return Number.isFinite(h) && h >= 0 && h <= 24 ? h : null;
}

function renderPills() {
  const box = $("#fac-pills");
  box.replaceChildren();
  FAC_AIRPORTS.forEach(([code, name]) => {
    const b = button(`${name} ${code}`, `pill${fac.code === code ? " on" : ""}`, () => loadFacility(code));
    b.setAttribute("role", "tab");
    box.appendChild(b);
  });
  const sel = el("select", null, `pill-select${FAC_OTHERS.some(([c]) => c === fac.code) ? " on" : ""}`);
  const first = el("option", "다른 공항");
  first.value = "";
  sel.appendChild(first);
  FAC_OTHERS.forEach(([code, name]) => {
    const o = el("option", `${name} ${code}`);
    o.value = code;
    if (fac.code === code) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener("change", () => { if (sel.value) loadFacility(sel.value); });
  box.appendChild(sel);
}

// --- CSS 3D 막대 (윗면 + 네 옆면) ---
function cuboid({ x, y, w, d, h, color, onHover, onClick, active }) {
  const c = el("div", null, `cub${active ? " active" : ""}`);
  c.style.width = `${w}px`;
  c.style.height = `${d}px`;
  c.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  c.style.setProperty("--h", `${Math.max(2, h)}px`);
  c.style.setProperty("--c", color);
  ["back", "left", "front", "right", "top"].forEach((f) => c.appendChild(el("div", null, `face f-${f}`)));
  if (onHover) c.addEventListener("mouseenter", onHover);
  if (onClick) {
    c.addEventListener("click", onClick);
    c.classList.add("clickable");
  }
  return c;
}

function makeStage(host, width, depth) {
  host.replaceChildren();
  const stage = el("div", null, "iso-stage");
  stage.style.width = `${width}px`;
  stage.style.height = `${depth}px`;
  stage.appendChild(el("div", null, "iso-floor"));
  host.appendChild(stage);
  let angle = Number(host.dataset.angle || 35);
  const apply = () => stage.style.setProperty("--angle", `${angle}deg`);
  apply();
  let dragging = null;
  host.onpointerdown = (e) => {
    if (e.target.closest(".clickable")) return;       // 막대를 누를 때는 회전하지 않는다
    dragging = { x: e.clientX, angle };
    host.setPointerCapture(e.pointerId);
    host.classList.add("dragging");
  };
  host.onpointermove = (e) => {
    if (!dragging) return;
    angle = dragging.angle + (e.clientX - dragging.x) * 0.4;
    host.dataset.angle = angle;
    apply();
  };
  host.onpointerup = () => { dragging = null; host.classList.remove("dragging"); };
  host.onpointercancel = host.onpointerup;
  return stage;
}

function floorLabel(stage, text, x, y, cls) {
  const l = el("div", text, `iso-label ${cls || ""}`);
  l.style.transform = `translate3d(${x}px, ${y}px, 1px)`;
  stage.appendChild(l);
}

function renderHours3D(points, info) {
  const host = $("#fac-iso");
  if (!points.length) {
    host.replaceChildren(el("p", "시간대별 자료가 없습니다.", "notice"));
    info.textContent = "";
    return;
  }
  const step = 24;
  const size = 17;
  const max = Math.max(...points.map((p) => p.value), 1);
  const stage = makeStage(host, step * 24, 40);
  const nowHour = kstHour();
  const colors = { ok: "#34d399", warn: "#fbbf24", bad: "#f87171", muted: "#cbd5e1" };
  points.forEach((p) => {
    const ratio = p.value / max;
    const isNow = p.hour === nowHour;
    stage.appendChild(cuboid({
      x: p.hour * step, y: 10, w: size, d: size, h: Math.round(ratio * 120),
      color: isNow ? "#2f7bd0" : colors[levelOf(ratio).cls], active: isNow,
      onHover: () => { info.textContent = `${p.hour}시 · ${p.label}${isNow ? " · 지금" : ""}`; },
    }));
    if (p.hour % 3 === 0) floorLabel(stage, `${p.hour}시`, p.hour * step - 2, 32);
  });
  const now = points.find((p) => p.hour === nowHour);
  info.textContent = now ? `지금(${nowHour}시): ${now.label} · 파란 막대가 지금 시간대입니다.` : "막대에 마우스를 올려 보십시오.";
}

function renderMatrix3D(summary) {
  const host = $("#fac-iso2");
  const info = $("#fac-iso2-info");
  const places = summary.by_place || {};
  const ORDER = ["T1", "탑승동", "T2", "국내선", "국제선", "기타"];
  const found = [...new Set(Object.keys(places).map((k) => k.split("|")[0]))];
  const terminals = [...ORDER.filter((t) => found.includes(t)), ...found.filter((t) => !ORDER.includes(t))];
  const cats = Object.keys(CAT_COLOR).filter((c) => (summary.by_category || {})[c]);
  if (!terminals.length || !cats.length) {
    host.replaceChildren(el("p", "입체로 보여 줄 시설 자료가 없습니다.", "notice"));
    info.textContent = "";
    return;
  }
  const grid = {};
  let max = 1;
  Object.entries(places).forEach(([key, counts]) => {
    const t = key.split("|")[0];
    Object.entries(counts).forEach(([cat, n]) => {
      grid[`${t}|${cat}`] = (grid[`${t}|${cat}`] || 0) + n;
      max = Math.max(max, grid[`${t}|${cat}`]);
    });
  });
  const step = 46;
  const stage = makeStage(host, step * cats.length + 60, step * terminals.length + 20);
  terminals.forEach((t, row) => {
    floorLabel(stage, t, -6, row * step + 14, "row");
    cats.forEach((cat, col) => {
      const n = grid[`${t}|${cat}`] || 0;
      if (!n) return;
      const active = fac.terminal === t && fac.category === cat;
      stage.appendChild(cuboid({
        x: 60 + col * step, y: row * step + 6, w: 28, d: 28, h: Math.round((n / max) * 95) + 4, color: CAT_COLOR[cat], active,
        onHover: () => { info.textContent = `${t} · ${cat} ${n}곳 (누르면 아래 목록을 이 조건으로 거릅니다)`; },
        onClick: () => {
          fac.terminal = active ? "" : t;
          fac.category = active ? "" : cat;
          fac.shown = 60;
          renderFacilityList();
          renderMatrix3D(summary);
        },
      }));
    });
  });
  cats.forEach((cat, col) => floorLabel(stage, cat, 60 + col * step - 4, terminals.length * step + 4, "col"));
  info.textContent = "터미널·구역(줄) × 시설 종류(색). 막대를 누르면 목록이 걸러지고, 빈 곳을 드래그하면 돌아갑니다.";
}

function metric(title, value, sub, lvl) {
  const box = el("div", null, `metric ${lvl ? lvl.cls : ""}`);
  box.append(el("div", title, "metric-title"), el("div", value, "metric-value"));
  if (sub) box.appendChild(el("div", sub, "metric-sub"));
  if (lvl) box.appendChild(el("span", lvl.text, `status ${lvl.cls}`));
  return box;
}

function renderNow(d) {
  const box = $("#fac-now");
  box.replaceChildren();
  $("#fac-title").textContent = `${d.airport_name} 지금 혼잡`;
  const modeBox = $("#fac-mode");
  modeBox.replaceChildren();
  const info = $("#fac-iso-info");
  if (d.airport === "ICN") {
    const part = d.parts.passenger;
    if (!part || !part.ok) {
      box.appendChild(el("p", `예상 승객 자료를 불러오지 못했습니다: ${part ? part.error.message : "자료 없음"}`, "notice error"));
      renderHours3D([], info);
      return;
    }
    const data = part.data;
    $("#fac-now-sub").textContent = "단계는 오늘 가장 붐비는 시간 대비 비율입니다.";
    const hourly = data.rows.filter((r) => r.hour != null);
    const now = hourly.find((r) => r.hour === data.current_hour) || hourly[0];
    if (now) {
      [["t1_departure", "T1 출국장"], ["t2_departure", "T2 출국장"], ["t1_arrival", "T1 입국장"], ["t2_arrival", "T2 입국장"]]
        .forEach(([k, label]) => {
          const ratio = now.ratio ? now.ratio[k] : null;
          box.appendChild(metric(`${label} · ${now.hour}시`, `${(now[k] || 0).toLocaleString()}명`,
            ratio != null ? `오늘 최대의 ${Math.round(ratio * 100)}%` : "", levelOf(ratio)));
        });
    } else {
      box.appendChild(el("p", "지금 시간대 자료가 없습니다.", "notice"));
    }
    ["departure", "arrival"].forEach((m) => modeBox.appendChild(button(m === "departure" ? "출국" : "입국",
      `seg-btn${fac.mode === m ? " on" : ""}`, () => { fac.mode = m; renderNow(d); })));
    const keyA = fac.mode === "departure" ? "t1_departure" : "t1_arrival";
    const keyB = fac.mode === "departure" ? "t2_departure" : "t2_arrival";
    renderHours3D(hourly.map((r) => {
      const a = r[keyA] || 0;
      const b = r[keyB] || 0;
      return { hour: r.hour, value: a + b, label: `${(a + b).toLocaleString()}명 (T1 ${a.toLocaleString()} · T2 ${b.toLocaleString()})` };
    }), info);
    return;
  }
  // ---- 한국공항공사 공항: 지금 = 실시간 구간별 단계(5곳만), 3D = 시간대별 이용 흐름(14곳 모두) ----
  const hourly = d.parts.hourly;
  const cong = d.parts.congestion;
  const nowHour = kstHour();
  const asLevel = (v) => {
    const n = levelNumber(v);
    return n == null ? { text: v || "자료 없음", cls: "muted" } : levelOf(Math.min(1, n / 3));
  };
  const subs = [];
  if (cong && cong.ok && cong.data.rows.length) {
    const cur = cong.data.rows[0];
    box.appendChild(metric(`국내선 전체 · ${cur.time || "지금"} 기준`, `${cur.level ?? "-"}단계`, "제공기관 실시간 단계", asLevel(cur.level)));
    (cong.data.segments || []).forEach((seg) => {
      if (cur[seg.key] != null) box.appendChild(metric(seg.label, `${cur[seg.key]}단계`, "", asLevel(cur[seg.key])));
    });
    Object.entries(cur.extra || {}).forEach(([k, v]) => {
      if (v !== "" && v != null && Number.isFinite(Number(v))) box.appendChild(metric(k, String(v), "제공기관 항목", null));
    });
  } else if (cong && !cong.ok) {
    box.appendChild(el("p", `실시간 혼잡도를 불러오지 못했습니다: ${cong.error.message}`, "notice error"));
  } else if (!cong) {
  }
  if (hourly && hourly.ok) {
    const h = hourly.data;
    const row = h.rows.find((r) => r.hour === nowHour);
    const max = Math.max(...h.rows.map((r) => r.value), 1);
    if (row) {
      const label = h.source === "expected_passengers" ? "예상 탑승객" : "출발·도착 편수";
      box.appendChild(metric(`${nowHour}시 ${label}`, `${row.value.toLocaleString()}${h.unit}`,
        h.source === "flights" ? `출발 ${row.departure} · 도착 ${row.arrival}` : `오늘 최대의 ${Math.round((row.value / max) * 100)}%`,
        levelOf(row.value / max)));
    }
    subs.push(h.note);
    if (h.fallback_reason) subs.push("시간대별 탑승승객 수 API 를 쓸 수 없어 편수로 대신 보여 드립니다.");
    ["all", "departure", "arrival"].forEach((m) => modeBox.appendChild(button({ all: "전체", departure: "출발", arrival: "도착" }[m],
      `seg-btn${(fac.kacMode || "all") === m ? " on" : ""}`, () => { fac.kacMode = m; renderNow(d); })));
    const mode = fac.kacMode || "all";
    renderHours3D(h.rows.map((r) => {
      const v = mode === "all" ? r.value : r[mode];
      return { hour: r.hour, value: v, label: `${v.toLocaleString()}${h.unit}${mode === "all" && h.source === "flights" ? ` (출발 ${r.departure} · 도착 ${r.arrival})` : ""}` };
    }), info);
  } else {
    box.appendChild(el("p", `시간대별 흐름을 불러오지 못했습니다: ${hourly ? hourly.error.message : "자료 없음"}`, "notice error"));
    renderHours3D([], info);
  }
  $("#fac-now-sub").textContent = subs.join(" ");
}

function facilityPart(d) {
  return d.airport === "ICN" ? d.parts.facilities : d.parts.commercial;
}

function renderFacilityList() {
  const d = fac.data;
  const list = $("#fac-list");
  const cats = $("#fac-cats");
  const areaBox = $("#fac-area");
  list.replaceChildren();
  cats.replaceChildren();
  areaBox.replaceChildren();
  $("#fac-more").hidden = true;
  if (!d) return;
  // 인천: 인천국제공항공사 상업시설 정보 / 그 외: 한국공항공사 전국공항 임대계약 정보 — 같은 모양으로 맞춰 함께 그린다
  const kac = d.airport !== "ICN";
  const part = facilityPart(d);
  $("#fac-iso2-wrap").hidden = false;
  $("#fac-keyword").hidden = false;
  if (!part || !part.ok) {
    list.appendChild(el("p", `시설 자료를 불러오지 못했습니다: ${part ? part.error.message : "자료 없음"}`, "notice error"));
    $("#fac-iso2").replaceChildren();
    $("#fac-iso2-info").textContent = "";
    $("#fac-list-sub").textContent = "";
    return;
  }
  const all = kac
    ? part.data.rows.map((r) => ({ ...r, items: r.business_type, terminal: r.zone, area: null, area_name: null, hours: null }))
    : part.data.rows;
  $("#fac-list-sub").textContent = kac
    ? `한국공항공사 전국공항 임대계약 정보 — ${d.airport_name} 승객용 매장 ${all.length.toLocaleString()}곳`
      + (part.data.hidden_business ? ` (화물·사무실 등 업무시설 ${part.data.hidden_business}곳 제외)` : "")
      + ". 구역·층은 임대 위치 글자에서 읽은 값이고, 운영 시간은 이 자료에 없습니다."
    : `인천국제공항공사 상업시설 정보 ${all.length.toLocaleString()}곳. 종류·터미널·층은 이름과 위치 글자에서 읽은 값입니다.`;
  if (!kac) {
    [["", "전체"], ["D", "출국장"], ["A", "입국장"]].forEach(([v, label]) =>
      areaBox.appendChild(button(label, `seg-btn${fac.area === v ? " on" : ""}`, () => { fac.area = v; fac.shown = 60; renderFacilityList(); })));
  }
  const kw = fac.keyword.trim().toLowerCase();
  const base = all.filter((r) => (!fac.area || r.area === fac.area)
    && (!fac.terminal || (r.terminal || "기타") === fac.terminal)
    && (!kw || [r.name, r.items, r.location, r.category].join(" ").toLowerCase().includes(kw)));
  const counts = {};
  base.forEach((r) => { counts[r.category] = (counts[r.category] || 0) + 1; });
  cats.appendChild(button(`전체 ${base.length}`, `chip-btn${fac.category ? "" : " on"}`, () => {
    fac.category = "";
    renderFacilityList();
    renderMatrix3D(part.data.summary);
  }));
  Object.keys(CAT_COLOR).filter((c) => counts[c]).forEach((c) => {
    const b = button(`${c} ${counts[c]}`, `chip-btn dot${fac.category === c ? " on" : ""}`, () => {
      fac.category = fac.category === c ? "" : c;
      fac.shown = 60;
      renderFacilityList();
      renderMatrix3D(part.data.summary);
    });
    b.style.setProperty("--dot", CAT_COLOR[c]);
    cats.appendChild(b);
  });
  if (fac.terminal) {
    cats.appendChild(button(`${fac.terminal} ✕`, "chip-btn on", () => {
      fac.terminal = "";
      renderFacilityList();
      renderMatrix3D(part.data.summary);
    }));
  }
  const rows = base.filter((r) => !fac.category || r.category === fac.category);
  rows.slice(0, fac.shown).forEach((r) => {
    const card = el("div", null, "fac-card");
    const cat = el("span", r.category, "fac-cat");
    cat.style.setProperty("--dot", CAT_COLOR[r.category] || "#94a3b8");
    const where = [r.terminal, r.floor, r.area_name].filter(Boolean).join(" · ");
    card.append(el("div", r.name || "-", "fac-name"), cat,
      el("div", r.location || "위치 정보 없음", "fac-loc"),
      el("div", [where, r.hours && `운영 ${r.hours}`, r.tel && `☎ ${r.tel}`].filter(Boolean).join("  ·  "), "fac-hours"));
    if (r.items) card.appendChild(el("div", r.items, "fac-items"));
    list.appendChild(card);
  });
  if (!rows.length) list.appendChild(el("p", "조건에 맞는 시설이 없습니다.", "notice"));
  if (rows.length > fac.shown) {
    const more = $("#fac-more");
    more.hidden = false;
    more.textContent = `더 보기 (${rows.length - fac.shown}곳 남음)`;
  }
}

$("#fac-more").addEventListener("click", () => { fac.shown += 60; renderFacilityList(); });
let facTimer = null;
$("#fac-keyword").addEventListener("input", (e) => {
  clearTimeout(facTimer);
  facTimer = setTimeout(() => { fac.keyword = e.target.value; fac.shown = 60; renderFacilityList(); }, 200);
});

async function loadFacility(code) {
  fac.code = code;
  fac.data = null;
  fac.category = "";
  fac.terminal = "";
  renderPills();
  $("#fac-now").replaceChildren(el("p", "불러오는 중입니다...", "notice"));
  $("#fac-iso").replaceChildren();
  $("#fac-iso2").replaceChildren();
  $("#fac-list").replaceChildren();
  const body = await api(`/airport-info/${code}`);
  if (fac.code !== code) return;                 // 그사이 다른 공항을 눌렀으면 버린다
  if (!body.ok) {
    $("#fac-now").replaceChildren(el("p", body.error.message, "notice error"));
    return;
  }
  fac.data = body.data;
  if (code === "ICN") track("facility"); else track("airport", code);
  renderNow(body.data);
  const fp = facilityPart(body.data);
  if (fp && fp.ok) {
    renderMatrix3D(fp.data.summary);
  }
  renderFacilityList();
}

renderPills();
