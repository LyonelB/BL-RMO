/* BL-RMO : analyseur en tiers d'octave et barres G/D des pages FM et DAB+ (SPECTRUM_PATCH).
 *
 * Les mesures arrivent du serveur toutes les 100 ms (/api/stream/spectrum) ;
 * l'affichage est animé ici : montée immédiate, descente progressive, crête
 * tenue 2 s par bande, puis qui retombe.
 */
(function () {
  const MIN = -96, HOLD_MS = 2000, BAR_FALL = 36, PEAK_FALL = 18;   // dB/s
  const LABELS = ['20','25','31.5','40','50','63','80','100','125','160','200','250','315','400','500','630','800',
                  '1k','1.25k','1.6k','2k','2.5k','3.15k','4k','5k','6.3k','8k','10k','12.5k','16k','20k'];

  function Spectrum(canvas) {
    const n = LABELS.length;
    const disp = new Array(n).fill(MIN), hold = new Array(n).fill(MIN), holdT = new Array(n).fill(0);
    let target = null, last = performance.now(), message = '', W = 0, H = 0, ctx = null;

    function size() {
      const dpr = window.devicePixelRatio || 1;
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    function draw(now) {
      const dt = Math.min(0.2, (now - last) / 1000); last = now;
      if (canvas.clientWidth !== W || canvas.clientHeight !== H || !ctx) size();
      for (let i = 0; i < n; i++) {
        const v = target ? Math.max(MIN, target[i]) : MIN;
        disp[i] = v >= disp[i] ? v : Math.max(v, disp[i] - BAR_FALL * dt);
        if (disp[i] >= hold[i]) { hold[i] = disp[i]; holdT[i] = now; }
        else if (now - holdT[i] > HOLD_MS) hold[i] = Math.max(disp[i], hold[i] - PEAK_FALL * dt);
      }
      const narrow = W < 560;
      const L = narrow ? 28 : 34, R = narrow ? 6 : 34, T = 10, B = narrow ? 20 : 34, pw = W - L - R, ph = H - T - B;
      const y = db => T + ph * (Math.min(0, Math.max(MIN, db)) / MIN);
      ctx.clearRect(0, 0, W, H);
      ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
      for (let d = 0; d >= MIN; d -= 12) {
        const yy = Math.round(y(d)) + 0.5;
        ctx.strokeStyle = d === 0 ? '#e2e8f0' : '#f1f5f9';
        ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(L + pw, yy); ctx.stroke();
        ctx.fillStyle = '#94a3b8';
        ctx.textAlign = 'right'; ctx.fillText(d, L - 6, yy + 3);
        if (!narrow) { ctx.textAlign = 'left'; ctx.fillText(d, L + pw + 6, yy + 3); }
      }
      const step = pw / n, bw = Math.max(2, step - (narrow ? 2 : 4)), seg = 3, gap = 1.5;
      for (let i = 0; i < n; i++) {
        const x = L + i * step + (step - bw) / 2, top = y(disp[i]);
        ctx.fillStyle = '#f8fafc'; ctx.fillRect(x, T, bw, ph);
        for (let s = T + ph; s - seg >= top - 0.01; s -= seg + gap) {
          const db = MIN * (s - seg - T) / ph;
          ctx.fillStyle = db > -12 ? '#ef4444' : db > -30 ? '#eab308' : '#22c55e';
          ctx.fillRect(x, s - seg, bw, seg);
        }
        if (target && hold[i] > MIN + 1) { ctx.fillStyle = '#334155'; ctx.fillRect(x, Math.round(y(hold[i])) - 1, bw, 2); }
        ctx.fillStyle = '#64748b'; ctx.textAlign = 'center';
        if (!narrow) ctx.fillText(LABELS[i], x + bw / 2, H - (i % 2 ? 6 : 19));
        else if (i % 3 === 0) ctx.fillText(LABELS[i], x + bw / 2, H - 6);
      }
      if (message) {
        ctx.fillStyle = 'rgba(255,255,255,.75)'; ctx.fillRect(L, T, pw, ph);
        ctx.fillStyle = '#64748b'; ctx.textAlign = 'center'; ctx.font = '13px Inter, system-ui, sans-serif';
        ctx.fillText(message, L + pw / 2, T + ph / 2);
      }
      requestAnimationFrame(draw);
    }
    requestAnimationFrame(draw);
    return {
      update(bands) { target = bands; message = ''; },
      clear(msg) { target = null; message = msg || ''; },
    };
  }

  // Barres G/D : crête, descente 20 dB/s, maintien de crête 1,5 s (comme le tableau de bord)
  function Meters(ids) {
    const st = [{ disp: -100, hold: -100, t: 0, v: -100 }, { disp: -100, hold: -100, t: 0, v: -100 }];
    const pct = d => Math.max(0, Math.min(100, (d + 60) / 60 * 100)) + '%';
    let last = performance.now(), stale = true;
    function frame(now) {
      const dt = Math.min(0.2, (now - last) / 1000); last = now;
      st.forEach((s, i) => {
        s.disp = s.v >= s.disp ? s.v : Math.max(s.v, s.disp - 20 * dt);
        if (s.v >= s.hold || now - s.t > 1500) { s.hold = s.v; s.t = now; }
        const id = ids[i];
        const bar = document.getElementById(id), pk = document.getElementById(id + 'p'), txt = document.getElementById(id + 'd');
        if (!bar) return;
        bar.style.width = pct(s.disp);
        bar.style.background = stale ? '#cbd5e1' : '#22c55e';
        if (pk) pk.style.left = pct(s.hold);
        if (txt) txt.textContent = s.hold > -60 ? s.hold.toFixed(1).replace('.', ',') : '-';
      });
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    return {
      set(l, r) { st[0].v = l; st[1].v = r; stale = false; },
      clear() { st[0].v = st[1].v = -100; stale = true; },
    };
  }

  // Flux temps réel : relance automatique si la connexion tombe
  function connect(src, sid, onData) {
    let es = null, closed = false, lastData = 0;
    function open() {
      if (closed) return;
      es = new EventSource(`/api/stream/spectrum?src=${encodeURIComponent(src)}` + (sid ? `&sid=${encodeURIComponent(sid)}` : ''));
      es.onmessage = ev => { lastData = Date.now(); try { onData(JSON.parse(ev.data)); } catch (e) {} };
      es.onerror = () => {
        es.close();
        // Message seulement si rien n'est revenu entre-temps (coupure réelle)
        setTimeout(() => { if (!closed && Date.now() - lastData > 3000) onData({ state: 'offline', message: 'Connexion perdue, nouvelle tentative…' }); }, 3500);
        setTimeout(open, 3000);
      };
    }
    open();
    return { close() { closed = true; if (es) es.close(); } };
  }

  // Bloc repliable : état mémorisé dans ce navigateur
  function collapsible(key, body, button, onChange) {
    let open = true;
    try { open = localStorage.getItem(key) !== 'closed'; } catch (e) {}
    const UP = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>';
    const DN = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>';
    function apply() {
      body.forEach(([el, whenOpen]) => el.classList.toggle('hidden', whenOpen !== open));
      button.innerHTML = open ? `${UP} Réduire` : `${DN} Afficher`;
      button.setAttribute('aria-expanded', String(open));
      if (onChange) onChange(open);
    }
    button.addEventListener('click', () => {
      open = !open;
      try { localStorage.setItem(key, open ? 'open' : 'closed'); } catch (e) {}
      apply();
    });
    apply();
    return { isOpen: () => open };
  }

  window.BLRMO = Object.assign(window.BLRMO || {}, { Spectrum, Meters, connectSpectrum: connect, collapsible, SPECTRUM_LABELS: LABELS });
})();
