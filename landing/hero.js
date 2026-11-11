// The hero: mail drifts in from the left, crosses a read-only scanner beam, is postmarked
// with its stage, and arcs into that stage's bin on the right. Plain 2D canvas; it pauses
// off-screen, and reduced motion gets one settled frame.
"use strict";
(() => {
  const cv = document.getElementById("mailstream");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STAGES = [["applied", "#6f9fc6", 5], ["assessment", "#e0a84a", 3], ["interview", "#5fbf8a", 3], ["offer", "#8fd16a", 1], ["rejected", "#e0604e", 4]];
  const TOTAL = STAGES.reduce((n, s) => n + s[2], 0);

  let W = 0, H = 0, beamX = 0, binX = 0, bins = [], mails = [], sparks = [], beam = 0, spawnIn = 0, visible = true, last = performance.now();
  const rand = (a, b) => a + Math.random() * (b - a);

  function size() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    W = cv.clientWidth; H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const wide = W > 860;
    beamX = wide ? W * 0.62 : W * 0.5;
    binX = W - 26;
    const top = wide ? H * 0.16 : H * 0.14, span = wide ? H * 0.66 : H * 0.72;
    bins = STAGES.map((s, i) => ({ color: s[1], y: top + (span / 4) * i, n: 0, flash: 0 }));
  }

  function pick() {
    let r = Math.random() * TOTAL;
    for (let i = 0; i < STAGES.length; i++) { r -= STAGES[i][2]; if (r <= 0) return i; }
    return 0;
  }

  function spawn() {
    const wide = W > 860;
    const lo = wide ? 0.1 : 0.1, hi = wide ? 0.9 : 0.9;
    mails.push({ x: -40, y0: H * rand(lo, hi), v: rand(90, 150), ph: rand(0, 6.28), stage: pick(), state: 0, p: 0, sx: 0, sy: 0 });
  }

  function step(dt, t) {
    beam = Math.max(0, beam - dt * 2.2);
    spawnIn -= dt;
    if (spawnIn <= 0) { spawn(); spawnIn = rand(0.22, 0.5); }
    for (const m of mails) {
      if (m.state === 0) {
        m.x += m.v * dt; m.y = m.y0 + Math.sin(t * 1.4 + m.ph) * 7;
        if (m.x >= beamX) {
          m.state = 1; m.sx = m.x; m.sy = m.y; beam = 1;
          for (let i = 0; i < 7; i++) sparks.push({ x: beamX, y: m.y, vx: rand(-30, 60), vy: rand(-60, 60), life: 1, c: STAGES[m.stage][1] });
        }
      } else {
        m.p += dt / 1.25;
        const b = bins[m.stage], k = m.p < 1 ? m.p : 1, e = k * k * (3 - 2 * k);
        const cx = m.sx + (binX - m.sx) * 0.45, cy = m.sy;
        const ix = (1 - e) * (1 - e) * m.sx + 2 * (1 - e) * e * cx + e * e * binX;
        const iy = (1 - e) * (1 - e) * m.sy + 2 * (1 - e) * e * cy + e * e * b.y;
        m.x = ix; m.y = iy;
        if (m.p >= 1) { b.n++; b.flash = 1; m.dead = true; }
      }
    }
    mails = mails.filter((m) => !m.dead);
    for (const s of sparks) { s.x += s.vx * dt; s.y += s.vy * dt; s.life -= dt * 2.2; }
    sparks = sparks.filter((s) => s.life > 0);
    for (const b of bins) b.flash = Math.max(0, b.flash - dt * 2.5);
  }

  function envelope(x, y, stroke, alpha, scale) {
    const w = 44 * scale, h = 30 * scale;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = "#211e1a"; ctx.strokeStyle = stroke; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 3); ctx.fill(); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - w / 2 + 1, y - h / 2 + 1); ctx.lineTo(x, y + 2 * scale); ctx.lineTo(x + w / 2 - 1, y - h / 2 + 1); ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    // the scanner beam
    const g = ctx.createLinearGradient(beamX - 40, 0, beamX + 40, 0);
    g.addColorStop(0, "rgba(224,168,74,0)"); g.addColorStop(0.5, `rgba(224,168,74,${0.1 + beam * 0.25})`); g.addColorStop(1, "rgba(224,168,74,0)");
    ctx.fillStyle = g; ctx.fillRect(beamX - 40, 0, 80, H);
    ctx.strokeStyle = `rgba(224,168,74,${0.45 + beam * 0.5})`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(beamX, 0); ctx.lineTo(beamX, H); ctx.stroke();
    ctx.font = "11px ui-monospace, Menlo, monospace"; ctx.fillStyle = "rgba(162,156,145,.8)"; ctx.textAlign = "center";
    ctx.fillText("gmail.readonly", beamX, 22);
    // bins
    ctx.textAlign = "right";
    for (const b of bins) {
      const len = 20 + Math.min(b.n, 16) * 5;
      ctx.fillStyle = b.color; ctx.globalAlpha = 0.25 + b.flash * 0.6;
      ctx.beginPath(); ctx.roundRect(binX + 12 - len, b.y - 5, len, 10, 5); ctx.fill();
      ctx.globalAlpha = 1;
      if (b.flash > 0) { ctx.shadowColor = b.color; ctx.shadowBlur = 18 * b.flash; ctx.beginPath(); ctx.arc(binX + 8, b.y, 5, 0, 6.3); ctx.fill(); ctx.shadowBlur = 0; }
    }
    // mail
    for (const m of mails) {
      if (m.state === 0) envelope(m.x, m.y, "#4a463f", 0.45 + Math.min(1, m.x / beamX) * 0.4, 1);
      else {
        const c = STAGES[m.stage][1];
        ctx.shadowColor = c; ctx.shadowBlur = 12;
        envelope(m.x, m.y, c, 1, 1 - m.p * 0.35);
        ctx.shadowBlur = 0;
        // the postmark, visible while it is still near the beam
        if (m.p < 0.35) { ctx.globalAlpha = 1 - m.p / 0.35; ctx.fillStyle = c; ctx.beginPath(); ctx.arc(m.x + 8, m.y - 2, 4, 0, 6.3); ctx.fill(); ctx.globalAlpha = 1; }
      }
    }
    for (const s of sparks) { ctx.globalAlpha = Math.max(0, s.life); ctx.fillStyle = s.c; ctx.fillRect(s.x, s.y, 2, 2); }
    ctx.globalAlpha = 1;
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (visible) { step(dt, now / 1000); draw(); }
    requestAnimationFrame(frame);
  }

  size();
  addEventListener("resize", size);
  // Start mid-flow: run the first ten seconds before the first frame is drawn.
  for (let i = 0; i < 300; i++) step(1 / 30, i / 30);
  if (reduce) {
    draw();
  } else {
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; }).observe(cv);
    requestAnimationFrame(frame);
  }
})();
