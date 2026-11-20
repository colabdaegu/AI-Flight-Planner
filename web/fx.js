// 화면 효과 모음 (app.js 와 독립). 여기서 실패해도 플래너 기능에는 영향이 없다.
//  1) 공항 전광판(스플릿 플랩) 제목   2) 메뉴의 미끄러지는 노란 표시   3) KST·UTC 시계
//  4) 커서를 따라가는 빛(스포트라이트)   5) 캔버스 3D 노선 지구본 (육지 점·대권 항로·비행 궤적)
(() => {
  "use strict";
  const reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s) => document.querySelector(s);
  const FX = (window.FX = window.FX || {});

  // ---------------------------------------------------------------- 1. 전광판 제목
  const HANGUL = "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후";
  const LATIN = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789";
  const isHangul = (ch) => /[가-힣]/.test(ch);
  const pick = (pool) => pool[Math.floor(Math.random() * pool.length)];
  let flapGen = 0;

  function flap(h, text) {
    if (!h) return;
    const gen = ++flapGen;
    const sr = document.createElement("span");
    sr.className = "sr-only";
    sr.textContent = text;
    const board = document.createElement("span");
    board.className = "flap";
    board.setAttribute("aria-hidden", "true");
    const cells = [];
    const words = text.split(" ");
    words.forEach((word, wi) => {
      const w = document.createElement("span");
      w.className = "flap-w";
      [...word].forEach((ch) => {
        const c = document.createElement("span");
        const hg = isHangul(ch);
        const lt = /[A-Za-z0-9]/.test(ch);
        c.className = `flap-c${wi === words.length - 1 && words.length > 1 ? " accent" : ""}`;
        c.dataset.ch = ch;
        c.dataset.pool = hg ? "h" : lt ? "l" : "";
        c.textContent = ch;
        w.appendChild(c);
        cells.push(c);
      });
      board.appendChild(w);
    });
    h.replaceChildren(sr, board);
    if (reduced) return;
    cells.forEach((c, i) => {
      const pool = c.dataset.pool === "h" ? HANGUL : c.dataset.pool === "l" ? LATIN : "";
      // 판 폭이 흔들리지 않게 최종 글자 폭으로 고정
      c.style.width = `${c.getBoundingClientRect().width}px`;
      if (!pool) { c.classList.add("land"); c.style.width = ""; return; }
      const spins = 3 + Math.floor(Math.random() * 4) + Math.floor(i * 0.5);
      let n = 0;
      const tick = () => {
        if (gen !== flapGen) return;
        c.classList.remove("spin", "land");
        void c.offsetWidth;
        if (n < spins) {
          c.textContent = pick(pool);
          c.classList.add("spin");
          n += 1;
          setTimeout(tick, 62);
        } else {
          c.textContent = c.dataset.ch;
          c.classList.add("land");
          setTimeout(() => { if (gen === flapGen) c.style.width = ""; }, 340);
        }
      };
      setTimeout(tick, i * 38);
    });
  }

  // 영문 안내 글자: 무작위 글자가 차례로 제자리를 찾는다
  function scramble(node, text) {
    if (!node) return;
    if (reduced) { node.textContent = text; return; }
    const gen = (node.__gen = (node.__gen || 0) + 1);
    let frame = 0;
    const total = text.length + 8;
    const step = () => {
      if (node.__gen !== gen) return;
      node.textContent = [...text].map((ch, i) => (ch === " " || i < frame - 6 ? ch : pick(LATIN))).join("");
      frame += 1;
      if (frame <= total) requestAnimationFrame(() => setTimeout(step, 22));
      else node.textContent = text;
    };
    step();
  }

  FX.headline = (btn) => {
    if (!btn) return;
    flap($("#headline"), btn.dataset.title || "");
    const sub = $("#subline");
    if (sub) {
      sub.textContent = btn.dataset.sub || "";
      sub.classList.remove("swap");
      void sub.offsetWidth;
      sub.classList.add("swap");
    }
    scramble($("#eyebrow"), btn.dataset.eyebrow || "");
  };

  // ---------------------------------------------------------------- 2. 메뉴 표시
  function setupTabInk() {
    const tabs = $(".tabs");
    if (!tabs) return;
    const ink = document.createElement("span");
    ink.className = "tab-ink";
    ink.setAttribute("aria-hidden", "true");
    tabs.prepend(ink);
    tabs.classList.add("has-ink");
    const place = () => {
      const a = tabs.querySelector("button.active");
      if (!a) return;
      ink.style.width = `${a.offsetWidth}px`;
      ink.style.transform = `translateX(${a.offsetLeft}px)`;
      if (!ink.classList.contains("on")) ink.classList.add("on");
      // 가로로 넘치는 좁은 화면: 고른 메뉴가 보이게 메뉴 줄만 옮긴다
      if (tabs.scrollWidth > tabs.clientWidth + 2) {
        const left = a.offsetLeft - (tabs.clientWidth - a.offsetWidth) / 2;
        tabs.scrollTo({ left, behavior: reduced ? "auto" : "smooth" });
      }
    };
    new MutationObserver((recs) => {
      if (recs.some((r) => r.target.tagName === "BUTTON")) place();
    }).observe(tabs, { subtree: true, attributes: true, attributeFilter: ["class"] });
    window.addEventListener("resize", place);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(place);
    place();
    FX.placeInk = place;
  }

  // ---------------------------------------------------------------- 3. 시계
  function setupClock() {
    const kst = $("#clock-kst");
    const utc = $("#clock-utc");
    if (!kst) return;
    const tick = () => {
      const now = new Date();
      kst.textContent = now.toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour12: false });
      if (utc) utc.textContent = now.toLocaleTimeString("en-GB", { timeZone: "UTC", hour12: false, hour: "2-digit", minute: "2-digit" });
    };
    tick();
    setInterval(tick, 1000);
  }

  // ---------------------------------------------------------------- 4. 스포트라이트
  function setupSpotlight() {
    if (reduced || !window.matchMedia("(hover: hover)").matches) return;
    const SEL = ".panel, .ticket, .fac-card, .log-card, .metric";
    let last = null;
    let raf = 0;
    let ev = null;
    const apply = () => {
      raf = 0;
      if (!ev) return;
      let node = ev.target instanceof Element ? ev.target.closest(SEL) : null;
      const hit = [];
      while (node && hit.length < 3) {
        const r = node.getBoundingClientRect();
        node.style.setProperty("--mx", `${ev.clientX - r.left}px`);
        node.style.setProperty("--my", `${ev.clientY - r.top}px`);
        hit.push(node);
        node = node.parentElement ? node.parentElement.closest(SEL) : null;
      }
      if (last) last.forEach((n) => { if (!hit.includes(n)) { n.style.removeProperty("--mx"); n.style.removeProperty("--my"); } });
      last = hit;
    };
    document.addEventListener("pointermove", (e) => {
      ev = e;
      if (!raf) raf = requestAnimationFrame(apply);
    }, { passive: true });
  }

  // ---------------------------------------------------------------- 5. 3D 노선 지구본
  const LAND_URL = window.FX_LAND_URL || "https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json";
  const AP = {
    ICN: [37.46, 126.44], GMP: [37.56, 126.8], CJU: [33.51, 126.49], PUS: [35.18, 128.94],
    NRT: [35.77, 140.39], HND: [35.55, 139.78], KIX: [34.43, 135.24], FUK: [33.59, 130.45], CTS: [42.78, 141.69], OKA: [26.2, 127.65],
    PVG: [31.14, 121.81], PEK: [40.08, 116.58], HKG: [22.31, 113.91], TPE: [25.08, 121.23], ULN: [47.65, 106.82], VVO: [43.4, 132.15],
    BKK: [13.69, 100.75], SGN: [10.82, 106.65], HAN: [21.22, 105.81], DAD: [16.04, 108.2], SIN: [1.36, 103.99], MNL: [14.51, 121.02],
    CEB: [10.31, 123.98], DPS: [-8.75, 115.17], SYD: [-33.94, 151.18], GUM: [13.48, 144.8], HNL: [21.32, -157.92],
    LAX: [33.94, -118.41], SFO: [37.62, -122.38], SEA: [47.45, -122.31], YVR: [49.19, -123.18], JFK: [40.64, -73.78],
    LHR: [51.47, -0.45], CDG: [49.01, 2.55], FRA: [50.03, 8.56], IST: [41.28, 28.75], DXB: [25.25, 55.36], DEL: [28.56, 77.1],
  };
  const HUB = "ICN";
  const ROUTES = Object.keys(AP).filter((c) => !["ICN", "GMP", "CJU", "PUS", "HND"].includes(c)).map((c) => [HUB, c])
    .concat([["GMP", "CJU"], ["PUS", "CJU"], ["GMP", "HND"], ["PUS", "FUK"], ["GMP", "KIX"]]);
  const LABELS = new Set(["ICN", "NRT", "LAX", "JFK", "SIN", "CDG", "SYD", "HNL", "DXB", "BKK", "SFO", "LHR", "HKG", "CJU", "DAD", "GUM", "FRA"]);

  const D2R = Math.PI / 180;
  const vec = (lat, lon) => [Math.cos(lat * D2R) * Math.cos(lon * D2R), Math.cos(lat * D2R) * Math.sin(lon * D2R), Math.sin(lat * D2R)];
  const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

  // TopoJSON → [다각형[고리[[경도, 위도]...]]]
  function topoPolygons(topo) {
    const tr = topo.transform;
    const arcs = topo.arcs.map((arc) => {
      let x = 0;
      let y = 0;
      return arc.map((p) => {
        if (tr) { x += p[0]; y += p[1]; return [x * tr.scale[0] + tr.translate[0], y * tr.scale[1] + tr.translate[1]]; }
        return [p[0], p[1]];
      });
    });
    const arcPts = (i) => (i >= 0 ? arcs[i] : arcs[~i].slice().reverse());
    const ring = (ids) => {
      const out = [];
      ids.forEach((i, k) => { const p = arcPts(i); for (let j = k ? 1 : 0; j < p.length; j += 1) out.push(p[j]); });
      return out;
    };
    const polys = [];
    const walk = (g) => {
      if (!g) return;
      if (g.type === "GeometryCollection") g.geometries.forEach(walk);
      else if (g.type === "Polygon") polys.push(g.arcs.map(ring));
      else if (g.type === "MultiPolygon") g.arcs.forEach((p) => polys.push(p.map(ring)));
    };
    Object.values(topo.objects).forEach(walk);
    return polys;
  }

  // 육지를 정거원통 도법으로 칠해 두고, 위도마다 고르게 점을 뿌려 육지 위 점만 남긴다
  function landDots(polys) {
    const W = 720;
    const H = 360;
    const cv = document.createElement("canvas");
    cv.width = W;
    cv.height = H;
    const c = cv.getContext("2d", { willReadFrequently: true });
    c.fillStyle = "#fff";
    polys.forEach((rings) => {
      c.beginPath();
      rings.forEach((r) => {
        // 날짜변경선을 가로지르는 고리(극 쪽 테두리 제외)는 띠가 생기므로 건너뛴다
        for (let i = 1; i < r.length; i += 1) {
          if (Math.abs(r[i][0] - r[i - 1][0]) > 300 && Math.abs(r[i][1]) < 85) return;
        }
        r.forEach(([lon, lat], i) => {
          const x = (lon + 180) * 2;
          const y = (90 - lat) * 2;
          if (i) c.lineTo(x, y); else c.moveTo(x, y);
        });
        c.closePath();
      });
      c.fill("evenodd");
    });
    const img = c.getImageData(0, 0, W, H).data;
    const land = (lat, lon) => {
      const x = Math.min(W - 1, Math.max(0, Math.round((lon + 180) * 2)));
      const y = Math.min(H - 1, Math.max(0, Math.round((90 - lat) * 2)));
      return img[(y * W + x) * 4 + 3] > 128;
    };
    const out = [];
    const STEP = 1.7;
    for (let lat = -78; lat <= 82; lat += STEP) {
      const n = Math.max(1, Math.round((360 * Math.cos(lat * D2R)) / STEP));
      for (let k = 0; k < n; k += 1) {
        const lon = -180 + (k + 0.5) * (360 / n);
        if (land(lat, lon)) out.push(vec(lat, lon));
      }
    }
    return out.length > 300 ? out : null;
  }

  // 자료를 못 받으면: 고르게 뿌린 점 구
  function sphereDots(n) {
    const out = [];
    const g = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < n; i += 1) {
      const z = 1 - (2 * (i + 0.5)) / n;
      const r = Math.sqrt(1 - z * z);
      out.push([Math.cos(g * i) * r, Math.sin(g * i) * r, z]);
    }
    return out;
  }

  function slerp(a, b, t, w) {
    const s = Math.sin(w);
    if (s < 1e-6) return a.slice();
    const k1 = Math.sin((1 - t) * w) / s;
    const k2 = Math.sin(t * w) / s;
    return [a[0] * k1 + b[0] * k2, a[1] * k1 + b[1] * k2, a[2] * k1 + b[2] * k2];
  }

  function setupGlobe() {
    const canvas = $("#globe");
    if (!canvas || !canvas.getContext) return;
    const wrap = canvas.parentElement;
    const ctx = canvas.getContext("2d");
    const hubV = vec(...AP[HUB]);
    let running = false;
    let visible = true;
    let enabled = true;
    let dots = sphereDots(1500);
    let near = [];
    let fallback = true;
    const markNear = () => { near = dots.map((v) => dot3(v, hubV) > Math.cos(9 * D2R)); };
    markNear();

    const arcs = ROUTES.map(([a, b], i) => {
      const A = vec(...AP[a]);
      const B = vec(...AP[b]);
      const w = Math.acos(Math.max(-1, Math.min(1, dot3(A, B))));
      const h = 0.025 + 0.2 * (w / Math.PI);
      const N = Math.max(14, Math.ceil(w * 46));
      const pts = [];
      for (let k = 0; k <= N; k += 1) {
        const t = k / N;
        const p = slerp(A, B, t, w);
        const r = 1 + h * Math.sin(Math.PI * t);
        pts.push([p[0] * r, p[1] * r, p[2] * r]);
      }
      return { pts, dest: B, dur: 3.2 + w * 3.2, off: (i * 1.37) % 7, local: a !== HUB };
    });
    const airports = Object.entries(AP).map(([code, ll]) => ({ code, v: vec(...ll) }));

    // 크기
    let W = 0;
    let H = 0;
    let dpr = 1;
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      const r = wrap.getBoundingClientRect();
      W = Math.max(10, Math.round(r.width * dpr));
      H = Math.max(10, Math.round(r.height * dpr));
      canvas.width = W;
      canvas.height = H;
    };
    resize();
    if (window.ResizeObserver) new ResizeObserver(() => { if (!enabled) return; resize(); if (!running) draw(performance.now()); }).observe(wrap);

    // 시점 (끌어서 돌리기 + 관성)
    let dragLon = 0;
    let dragLat = 0;
    let vel = 0;
    let drag = null;
    canvas.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY, lon: dragLon, lat: dragLat, t: performance.now() };
      vel = 0;
      canvas.setPointerCapture(e.pointerId);
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const nl = drag.lon - (e.clientX - drag.x) * 0.32;
      const now = performance.now();
      vel = (nl - dragLon) / Math.max(8, now - (drag.t || now)) * 16;
      drag.t = now;
      dragLon = nl;
      dragLat = Math.max(-30, Math.min(30, drag.lat + (e.clientY - drag.y) * 0.2));
    });
    const end = () => { drag = null; canvas.classList.remove("dragging"); };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);

    // 땅 자료 (실패하면 점 구 그대로)
    const ac = window.AbortController ? new AbortController() : null;
    const timer = setTimeout(() => ac && ac.abort(), 8000);
    fetch(LAND_URL, ac ? { signal: ac.signal } : {})
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((topo) => {
        const d = landDots(topoPolygons(topo));
        if (d) { dots = d; fallback = false; markNear(); if (!running) draw(performance.now()); }
      })
      .catch(() => {})
      .finally(() => clearTimeout(timer));

    const BINS = 6;
    const bins = Array.from({ length: BINS }, () => []);

    function draw(now) {
      const t = now / 1000;
      if (!drag && Math.abs(vel) > 0.001) { dragLon += vel; vel *= 0.94; }
      const lon0 = 138 + (reduced ? 0 : 26 * Math.sin((t * 2 * Math.PI) / 80)) + dragLon;
      const lat0 = 26 + dragLat;
      const sl = Math.sin(lon0 * D2R);
      const cl = Math.cos(lon0 * D2R);
      const sp = Math.sin(lat0 * D2R);
      const cp = Math.cos(lat0 * D2R);
      const E = [-sl, cl, 0];
      const Nn = [-sp * cl, -sp * sl, cp];
      const F = [cp * cl, cp * sl, sp];
      const R = Math.min(W, H) * 0.37;
      const cx = W * 0.5;
      const cy = H * 0.42;
      const P = (v) => [dot3(v, E), dot3(v, Nn), dot3(v, F)];

      ctx.clearRect(0, 0, W, H);

      // 대기광
      let g = ctx.createRadialGradient(cx, cy, R * 0.92, cx, cy, R * 1.32);
      g.addColorStop(0, "rgba(92,200,255,0.20)");
      g.addColorStop(0.35, "rgba(92,200,255,0.07)");
      g.addColorStop(1, "rgba(92,200,255,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.35, 0, Math.PI * 2);
      ctx.fill();

      // 구 몸통
      g = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 0.05, cx, cy, R);
      g.addColorStop(0, "#15263f");
      g.addColorStop(0.6, "#0b1527");
      g.addColorStop(1, "#060c18");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();

      // 경위선
      ctx.lineWidth = 1 * dpr;
      ctx.strokeStyle = "rgba(120,170,230,0.07)";
      ctx.beginPath();
      for (let lon = -180; lon < 180; lon += 30) {
        let pen = false;
        for (let lat = -84; lat <= 84; lat += 6) {
          const q = P(vec(lat, lon));
          if (q[2] <= 0) { pen = false; continue; }
          const x = cx + q[0] * R;
          const y = cy - q[1] * R;
          if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
          pen = true;
        }
      }
      for (let lat = -60; lat <= 60; lat += 30) {
        let pen = false;
        for (let lon = -180; lon <= 180; lon += 5) {
          const q = P(vec(lat, lon));
          if (q[2] <= 0) { pen = false; continue; }
          const x = cx + q[0] * R;
          const y = cy - q[1] * R;
          if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
          pen = true;
        }
      }
      ctx.stroke();

      // 육지 점 (깊이별로 묶어 그린다)
      bins.forEach((b) => { b.length = 0; });
      const hot = [];
      for (let i = 0; i < dots.length; i += 1) {
        const v = dots[i];
        const z = dot3(v, F);
        if (z <= 0.02) continue;
        const x = cx + dot3(v, E) * R;
        const y = cy - dot3(v, Nn) * R;
        if (near[i]) { hot.push(x, y, z); continue; }
        bins[Math.min(BINS - 1, Math.floor(z * BINS))].push(x, y);
      }
      const base = fallback ? 0.55 : 1;
      bins.forEach((b, bi) => {
        const z = (bi + 0.5) / BINS;
        const s = (fallback ? 1.1 : 1.35 + z * 0.9) * dpr;
        ctx.fillStyle = `rgba(165,195,235,${((0.12 + z * 0.62) * base).toFixed(3)})`;
        for (let k = 0; k < b.length; k += 2) ctx.fillRect(b[k] - s / 2, b[k + 1] - s / 2, s, s);
      });
      for (let k = 0; k < hot.length; k += 3) {
        const s = 2.1 * dpr;
        ctx.fillStyle = `rgba(255,198,41,${(0.35 + hot[k + 2] * 0.55).toFixed(3)})`;
        ctx.fillRect(hot[k] - s / 2, hot[k + 1] - s / 2, s, s);
      }

      // 가장자리 빛
      g = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
      g.addColorStop(0, "rgba(140,210,255,0.55)");
      g.addColorStop(0.5, "rgba(140,210,255,0.06)");
      g.addColorStop(1, "rgba(140,210,255,0.0)");
      ctx.strokeStyle = g;
      ctx.lineWidth = 1.4 * dpr;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.stroke();

      // 항로
      const seen = (q) => q[2] > 0 || q[0] * q[0] + q[1] * q[1] > 1;
      ctx.lineCap = "round";
      arcs.forEach((a) => {
        const pr = a.pts.map(P);
        ctx.strokeStyle = a.local ? "rgba(92,200,255,0.28)" : "rgba(255,198,41,0.2)";
        ctx.lineWidth = 1 * dpr;
        ctx.beginPath();
        let pen = false;
        pr.forEach((q) => {
          if (!seen(q)) { pen = false; return; }
          const x = cx + q[0] * R;
          const y = cy - q[1] * R;
          if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
          pen = true;
        });
        ctx.stroke();

        // 비행 궤적: 머리 + 꼬리
        const cycle = a.dur + 1.6;
        const ph = reduced ? 0.6 * a.dur : ((t + a.off) % cycle);
        const p = ph / a.dur;
        const n = pr.length - 1;
        if (p <= 1) {
          const head = p * n;
          const tail = Math.max(0, head - n * 0.22);
          for (let k = Math.floor(tail); k < Math.floor(head); k += 1) {
            const q1 = pr[k];
            const q2 = pr[k + 1];
            if (!seen(q1) || !seen(q2)) continue;
            const al = (k - tail) / Math.max(1, head - tail);
            ctx.strokeStyle = a.local ? `rgba(140,215,255,${(al * 0.95).toFixed(3)})` : `rgba(255,214,92,${(al * 0.95).toFixed(3)})`;
            ctx.lineWidth = (0.8 + al * 1.6) * dpr;
            ctx.beginPath();
            ctx.moveTo(cx + q1[0] * R, cy - q1[1] * R);
            ctx.lineTo(cx + q2[0] * R, cy - q2[1] * R);
            ctx.stroke();
          }
          const qi = pr[Math.min(n, Math.round(head))];
          if (seen(qi)) {
            const x = cx + qi[0] * R;
            const y = cy - qi[1] * R;
            const hg = ctx.createRadialGradient(x, y, 0, x, y, 6 * dpr);
            hg.addColorStop(0, "rgba(255,255,255,0.95)");
            hg.addColorStop(0.35, a.local ? "rgba(92,200,255,0.7)" : "rgba(255,198,41,0.7)");
            hg.addColorStop(1, "rgba(255,198,41,0)");
            ctx.fillStyle = hg;
            ctx.beginPath();
            ctx.arc(x, y, 6 * dpr, 0, Math.PI * 2);
            ctx.fill();
          }
        } else {
          // 도착: 물결
          const q = P(a.dest);
          if (q[2] > 0) {
            const k = (ph - a.dur) / 1.6;
            ctx.strokeStyle = `rgba(255,198,41,${(0.7 * (1 - k)).toFixed(3)})`;
            ctx.lineWidth = 1.2 * dpr;
            ctx.beginPath();
            ctx.arc(cx + q[0] * R, cy - q[1] * R, (2 + k * 12) * dpr, 0, Math.PI * 2);
            ctx.stroke();
          }
        }
      });

      // 공항 점 · 이름
      ctx.textBaseline = "middle";
      airports.forEach(({ code, v }) => {
        const q = P(v);
        if (q[2] <= 0.05) return;
        const x = cx + q[0] * R;
        const y = cy - q[1] * R;
        if (code === HUB) {
          for (let r = 0; r < 2; r += 1) {
            const k = reduced ? 0.4 : ((t * 0.7 + r * 0.5) % 1);
            ctx.strokeStyle = `rgba(255,198,41,${(0.8 * (1 - k)).toFixed(3)})`;
            ctx.lineWidth = 1.5 * dpr;
            ctx.beginPath();
            ctx.arc(x, y, (4 + k * 22) * dpr, 0, Math.PI * 2);
            ctx.stroke();
          }
          ctx.fillStyle = "#ffc629";
          ctx.beginPath();
          ctx.arc(x, y, 3.6 * dpr, 0, Math.PI * 2);
          ctx.fill();
          ctx.font = `800 ${12 * dpr}px "JetBrains Mono Variable", "JetBrains Mono", Consolas, monospace`;
          ctx.fillStyle = "rgba(255,214,92,0.98)";
          ctx.fillText("ICN", x + 10 * dpr, y - 9 * dpr);
          return;
        }
        ctx.fillStyle = `rgba(233,238,247,${(0.35 + q[2] * 0.6).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(x, y, 1.9 * dpr, 0, Math.PI * 2);
        ctx.fill();
        if (LABELS.has(code) && q[2] > 0.22) {
          ctx.font = `600 ${10 * dpr}px "JetBrains Mono Variable", "JetBrains Mono", Consolas, monospace`;
          ctx.fillStyle = `rgba(200,215,235,${(q[2] * 0.75).toFixed(3)})`;
          ctx.fillText(code, x + 6 * dpr, y);
        }
      });
    }

    // 화면에 보일 때만 움직인다
    const loop = (now) => {
      if (!running) return;
      draw(now);
      requestAnimationFrame(loop);
    };
    const update = () => {
      const should = enabled && !reduced && visible && !document.hidden;
      if (should && !running) { running = true; requestAnimationFrame(loop); }
      if (!should) running = false;
    };
    if (window.IntersectionObserver) {
      new IntersectionObserver((es) => { visible = es[0].isIntersecting; update(); }).observe(canvas);
    }
    document.addEventListener("visibilitychange", update);
    draw(performance.now());
    wrap.classList.add("ready");
    update();
    return {
      // 끄면 그리기를 멈추고 캔버스 메모리(픽셀 버퍼)를 비운다. 켜면 크기를 다시 잡고 이어서 그린다.
      off() {
        enabled = false;
        running = false;
        wrap.classList.add("off");
        canvas.width = 0;
        canvas.height = 0;
      },
      on() {
        enabled = true;
        wrap.classList.remove("off");
        resize();
        draw(performance.now());
        update();
      },
    };
  }

  // ---------------------------------------------------------------- 지구본 켜고 끄기 (오른쪽 위 버튼, 이 브라우저에 기억)
  const GLOBE_KEY = "planner-globe";
  function setupGlobeToggle() {
    const btn = $("#globe-toggle");
    const wrap = document.querySelector(".globe-wrap");
    let ctl = null;
    let on = true;
    try { on = localStorage.getItem(GLOBE_KEY) !== "off"; } catch (e) { /* 저장소를 못 쓰면 켠 상태 */ }
    const apply = () => {
      if (on) {
        if (!ctl) ctl = setupGlobe();       // 꺼 둔 채로 열었으면 처음 켤 때 만든다
        else ctl.on();
      } else if (ctl) {
        ctl.off();
      } else if (wrap) {
        wrap.classList.add("off");
      }
      if (btn) {
        btn.setAttribute("aria-pressed", String(on));
        btn.classList.toggle("is-off", !on);
        btn.title = on ? "지구본 끄기 (메모리·전력 절약)" : "지구본 켜기";
        const st = btn.querySelector(".gt-state");
        if (st) st.textContent = on ? "켜짐" : "꺼짐";
      }
    };
    if (btn) {
      btn.addEventListener("click", () => {
        on = !on;
        try { localStorage.setItem(GLOBE_KEY, on ? "on" : "off"); } catch (e) { /* 무시 */ }
        apply();
      });
    }
    apply();
  }

  // ---------------------------------------------------------------- 시작
  const init = () => {
    const tryRun = (fn) => { try { fn(); } catch (e) { /* 효과 실패는 무시 */ } };
    tryRun(setupTabInk);
    tryRun(setupClock);
    tryRun(setupSpotlight);
    tryRun(setupGlobeToggle);
    tryRun(() => FX.headline(document.querySelector(".tabs button.active")));
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
