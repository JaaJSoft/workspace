/*
 * Presentation stage: builds the scenes described by window.TIMELINE once,
 * then paints any instant with render(t).
 *
 * render(t) is a pure function of t - no CSS transitions, no running clock,
 * no unseeded randomness - so the renderer can step through frames in any
 * order and get the same pixels the live player shows at that instant.
 *
 *   index.html          live player with the soundtrack (space, arrows, click)
 *   index.html?t=42     paused on second 42
 *   index.html?render   bare stage for scripts/presentation_video.py
 */
(() => {
  'use strict';

  const T = window.TIMELINE;
  const params = new URLSearchParams(location.search);
  const RENDER = params.has('render');
  const BEAT = 60 / T.bpm;
  const BAR = BEAT * T.beatsPerBar;
  const HOST = 'workspace.example.com';

  const stageEl = document.getElementById('stage');
  stageEl.style.setProperty('--brand-from', T.brand[0]);
  stageEl.style.setProperty('--brand-to', T.brand[1]);

  // -- math -------------------------------------------------------------------

  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, k) => a + (b - a) * k;
  const prog = (t, start, dur) => clamp((t - start) / dur);
  const E = {
    linear: k => k,
    outCubic: k => 1 - (1 - k) ** 3,
    inCubic: k => k ** 3,
    inOutCubic: k => (k < 0.5 ? 4 * k ** 3 : 1 - (-2 * k + 2) ** 3 / 2),
    inOutSine: k => -(Math.cos(Math.PI * k) - 1) / 2,
    outExpo: k => (k >= 1 ? 1 : 1 - 2 ** (-10 * k)),
    outBack: k => 1 + 2.70158 * (k - 1) ** 3 + 1.70158 * (k - 1) ** 2,
    // Settles on 1 with one visible overshoot.
    spring: k => (k >= 1 ? 1 : 1 - Math.exp(-6.5 * k) * Math.cos(10.5 * k)),
  };
  const tw = (t, start, dur, ease = E.outExpo) => ease(prog(t, start, dur));

  const rgb = hex => {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };
  const mix = (a, b, k) => a.map((v, i) => Math.round(lerp(v, b[i], k)));
  const cssRgb = c => c.join(' ');

  function prng(seed) {
    return () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let r = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  // -- dom --------------------------------------------------------------------

  function h(tag, cls, parent, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  // Lucide swaps the <i> for an <svg>, so callers keep the wrapper.
  function icon(name, parent, cls = '') {
    const wrap = h('span', cls, parent);
    wrap.style.display = 'grid';
    wrap.style.placeItems = 'center';
    h('i', null, wrap).dataset.lucide = name;
    return wrap;
  }

  function tile(iconName, color, parent, cls = '') {
    const e = h('div', `tile ${cls}`, parent);
    if (color) e.style.setProperty('--mc', cssRgb(rgb(color)));
    h('i', null, e).dataset.lucide = iconName;
    return e;
  }

  function letters(text, parent, { masked = true, gradient = false } = {}) {
    const holder = masked ? h('span', 'line', parent) : parent;
    return [...text].map(ch =>
      h('span', `ch${gradient ? ' gradient-text' : ''}`, holder, ch === ' ' ? ' ' : ch),
    );
  }

  function words(text, parent) {
    return text.split(' ').map((word, i, all) => {
      const w = h('span', 'w', parent, word);
      if (i < all.length - 1) parent.appendChild(document.createTextNode(' '));
      return w;
    });
  }

  function place(e, x, y, w, hgt) {
    e.style.left = `${x}px`;
    e.style.top = `${y}px`;
    if (w != null) e.style.width = `${w}px`;
    if (hgt != null) e.style.height = `${hgt}px`;
  }

  function style(e, opacity, transform, blur) {
    e.style.opacity = opacity.toFixed(4);
    if (transform != null) e.style.transform = transform;
    if (blur != null) e.style.filter = blur > 0.05 ? `blur(${blur.toFixed(2)}px)` : 'none';
  }

  // Letters rising out of their mask, staggered.
  function riseLetters(chars, lt, start, stagger = 0.035, dur = 0.75) {
    chars.forEach((c, i) => {
      const k = tw(lt, start + i * stagger, dur);
      style(c, clamp(k * 1.6), `translateY(${((1 - k) * 105).toFixed(2)}%)`);
    });
  }

  // Words fading up out of a blur, staggered.
  function blurWords(list, lt, start, stagger = 0.055, dur = 0.8) {
    list.forEach((w, i) => {
      const k = tw(lt, start + i * stagger, dur, E.outCubic);
      style(w, k, `translateY(${((1 - k) * 26).toFixed(2)}px)`, (1 - k) * 10);
    });
  }

  // -- background, hud, transitions --------------------------------------------

  const bg = h('div', 'layer', stageEl);
  const blobs = ['a', 'b', 'c'].map(k => h('div', `blob ${k}`, bg));
  const floor = h('div', 'floor', bg);
  h('div', 'layer dots', bg);
  const scenesLayer = h('div', 'layer', stageEl);
  const hud = h('div', 'layer', stageEl);
  const wipeLayer = h('div', 'layer', stageEl);
  const grain = h('div', 'layer grain', stageEl);
  h('div', 'layer vignette', stageEl);
  const fade = h('div', 'layer fade', stageEl);

  const hudLogo = h('div', 'hud-logo', hud);
  tile('users', null, hudLogo, 'logo-tile');
  h('span', null, hudLogo, 'Workspace');
  const hudDots = h('div', 'hud-dots', hud);
  const dots = T.modules.map(() => h('div', 'hud-dot', hudDots));

  const wipes = [h('div', 'wipe', wipeLayer), h('div', 'wipe', wipeLayer)];

  // -- scenes -------------------------------------------------------------------

  const sections = T.sections.map((data, index) => {
    const root = h('div', 'layer scene', scenesLayer);
    const base = {
      ...data,
      index,
      root,
      dur: data.bars * BAR,
      rgb: rgb(data.color),
      drumEnd: (data.drumBars || 0) * BAR,
    };
    root.style.setProperty('--mc', cssRgb(base.rgb));
    const build = { intro: buildIntro, module: buildModule, platform: buildPlatform, outro: buildOutro };
    base.update = build[data.kind](base, root);
    return base;
  });
  const moduleSections = sections.filter(s => s.kind === 'module');

  function buildIntro(s, root) {
    const flare = h('div', 'flare', root);
    const group = h('div', 'layer', root);
    const logo = tile('users', null, group, 'hero-logo logo-tile');
    place(logo, null, 276);
    logo.style.left = '50%';
    const rings = [0, 1].map(() => h('div', 'ring', logo));
    const title = h('div', 'center-stack hero-title', group);
    title.style.top = '490px';
    const chars = letters(s.title, title, { masked: false, gradient: true });
    const tag = h('div', 'center-stack hero-tagline', group);
    tag.style.top = '700px';
    const tagWords = words(s.tagline, tag);

    const sub = h('div', 'center-stack hero-sub', root);
    sub.style.top = '800px';
    const subWords = words(s.subline, sub);

    const rand = prng(7);
    const n = T.modules.length;
    const gap = 30;
    const rowW = n * 92 + (n - 1) * gap;
    const tiles = T.modules.map((m, i) => {
      const e = tile(m.icon, m.color, root, 'orbit-tile');
      const angle = rand() * Math.PI * 2;
      return {
        e,
        x: 960 - rowW / 2 + i * (92 + gap),
        y: 640,
        fromX: Math.cos(angle) * 1300,
        fromY: Math.sin(angle) * 900,
        spin: (rand() - 0.5) * 540,
      };
    });

    return lt => {
      const exit = tw(lt, s.dur - 0.8, 0.8, E.inCubic);

      const f = tw(lt, 0.2, 1.2, E.outCubic);
      const fOut = tw(lt, 1.5, 0.8, E.outCubic);
      style(flare, f * (1 - fOut), `scaleX(${lerp(0.02, 1, f).toFixed(3)}) scaleY(${lerp(1, 0.3, fOut).toFixed(3)})`);

      const shift = tw(lt, 3.8, 1.1, E.inOutCubic);
      style(
        group,
        1 - exit,
        `translateY(${(-190 * shift).toFixed(1)}px) scale(${lerp(1, 0.82, shift) * lerp(1, 1.6, exit)})`,
        exit * 16,
      );

      const k = tw(lt, 0.6, 1.2, E.spring);
      style(logo, clamp(k * 3), `scale(${k.toFixed(4)}) rotate(${((1 - k) * -120).toFixed(2)}deg)`);
      rings.forEach((r, i) => {
        const p = tw(lt, 1.15 + i * 0.25, 1.3, E.outCubic);
        style(r, (lt > 1.15 + i * 0.25 ? 1 - p : 0) * 0.8, `scale(${lerp(1, 2.4, p).toFixed(3)})`);
      });

      chars.forEach((c, i) => {
        const p = tw(lt, 1.5 + i * 0.06, 1.1);
        style(c, p, `translateY(${((1 - p) * 70).toFixed(1)}px) scale(${lerp(1.3, 1, p).toFixed(3)})`, (1 - p) * 16);
      });

      blurWords(tagWords, lt, 2.5, 0.08, 0.9);
      tag.style.opacity = (1 - tw(lt, 3.7, 0.5, E.outCubic)).toFixed(3);

      blurWords(subWords, lt, 5.1, 0.05, 0.8);
      sub.style.opacity = (1 - exit).toFixed(3);

      tiles.forEach((o, i) => {
        const p = tw(lt, 4.1 + i * 0.07, 1.2, E.spring);
        const x = lerp(o.fromX, 0, p) + (o.x - 960) * exit * 0.9;
        const y = lerp(o.fromY, 0, p) + (o.y - 540) * exit * 1.2;
        style(
          o.e,
          clamp(p * 2) * (1 - exit),
          `translate(${(o.x + x).toFixed(1)}px, ${(o.y + y).toFixed(1)}px) rotate(${((1 - p) * o.spin).toFixed(1)}deg) scale(${lerp(0.4, 1, clamp(p)) * lerp(1, 1.8, exit)})`,
          exit * 10,
        );
      });
    };
  }

  function buildModule(s, root) {
    const dir = s.number % 2 === 1 ? 1 : -1;
    if (dir < 0) root.classList.add('flip');
    const winX = dir > 0 ? 760 : 100;
    const winY = 178;

    const text = h('div', 'm-text', root);
    text.style.left = `${dir > 0 ? 130 : 1180}px`;
    const counter = h('div', 'm-counter', text);
    h('span', null, counter, String(s.number).padStart(2, '0'));
    h('span', 'bar', counter);
    h('span', 'total', counter, String(moduleCount()).padStart(2, '0'));
    if (s.preview) h('span', 'badge', counter, 'Preview');
    const iconBox = h('div', 'm-icon', text);
    const iconTile = tile(s.icon, s.color, iconBox);
    iconTile.style.width = iconTile.style.height = '100%';
    const iconRing = h('div', 'ring', iconBox);
    const name = h('div', 'm-name', text);
    const nameChars = letters(s.name, name, { gradient: true });
    const tagline = h('div', 'm-tagline', text);
    const tagWords = words(s.tagline, tagline);
    const featBox = h('div', 'm-features', text);
    const feats = s.features.map(([ic, label]) => {
      const row = h('div', 'feat', featBox);
      const box = h('div', 'feat-ic', row);
      h('i', null, box).dataset.lucide = ic;
      h('span', null, row, label);
      return { row, box };
    });

    const glow = h('div', 'm-glow', root);
    glow.style.left = `${winX + 30}px`;
    const win = h('div', 'm-window', root);
    place(win, winX, winY);
    const chrome = h('div', 'chrome', win);
    ['#ff5f57', '#febc2e', '#28c840'].forEach(c => (h('span', 'dot', chrome).style.background = c));
    const url = h('div', 'url', chrome);
    icon('lock', url);
    h('span', null, url, HOST);
    const urlPath = h('span', 'path', url);
    h('span', 'dot', chrome).style.visibility = 'hidden';
    const screen = h('div', 'screen', win);

    const scale = 1060 / T.capture.width;
    const shotDur = s.dur / s.shots.length;
    const shots = s.shots.map((shot, i) => {
      const img = h('img', null, screen);
      img.src = shot.src;
      let callout = null;
      if (shot.callout) {
        const [cx, cy, cw, ch] = shot.callout;
        const k = Math.min(1.5, 560 / (cw * scale));
        const size = scale * k;
        callout = h('div', 'callout', root);
        const w = cw * size;
        const hh = ch * size;
        place(callout, dir > 0 ? winX - 70 : winX + 1060 + 70 - w, winY + 707 - hh * 0.55, w, hh);
        callout.style.backgroundImage = `url("${shot.src}")`;
        callout.style.backgroundSize = `${T.capture.width * size}px ${T.capture.height * size}px`;
        callout.style.backgroundPosition = `${-cx * size}px ${-cy * size}px`;
      }
      return { img, callout, start: i * shotDur, path: shot.path };
    });

    return lt => {
      const out = tw(lt, s.dur - 0.55, 0.55, E.inCubic);

      // text column
      style(text, 1 - out, `translateY(${(-40 * out).toFixed(1)}px)`, out * 8);
      const c = tw(lt, 0.0, 0.7);
      style(counter, c, `translateX(${((1 - c) * -30).toFixed(1)}px)`);
      const k = tw(lt, 0.05, 0.95, E.spring);
      style(iconTile, clamp(k * 3), `scale(${k.toFixed(4)}) rotate(${((1 - k) * -30).toFixed(2)}deg)`);
      const r = tw(lt, 0.5, 1.0, E.outCubic);
      style(iconRing, lt > 0.5 ? (1 - r) * 0.9 : 0, `scale(${lerp(1, 1.9, r).toFixed(3)})`);
      riseLetters(nameChars, lt, 0.18);
      blurWords(tagWords, lt, 0.7);
      feats.forEach((f, i) => {
        const at = 1.5 + i * BEAT;
        const p = tw(lt, at, 0.65, E.outBack);
        style(f.row, clamp((lt - at) / 0.25), `translateX(${((1 - p) * -44).toFixed(1)}px) scale(${lerp(0.92, 1, clamp(p)).toFixed(3)})`);
        const flash = lt > at ? Math.exp(-(lt - at) / 0.35) : 0;
        f.box.style.boxShadow = `0 0 ${(30 * flash).toFixed(1)}px ${(6 * flash).toFixed(1)}px rgb(var(--mc) / ${(0.6 * flash).toFixed(3)})`;
      });

      // window
      const w = tw(lt, 0.1, 1.5);
      const float = Math.sin(lt * 1.2) * 7;
      // Enters from its own side of the frame and leaves the same way,
      // never across the text column.
      const rotY = dir * (lerp(-40, -13, w) + (lt / s.dur) * 6 - out * 22);
      const tx = dir * ((1 - w) * 560 + out * 460);
      const tz = (1 - w) * -380 - out * 650;
      // The page itself is never cropped or zoomed: the whole window
      // breathes a little instead, so the app always reads as the app.
      const breathe = lerp(1, 1.03, E.inOutSine(prog(lt, 0.8, s.dur - 0.8)));
      style(
        win,
        clamp(w * 2.5) * (1 - out),
        `translate3d(${tx.toFixed(1)}px, ${float.toFixed(1)}px, ${tz.toFixed(1)}px) rotateY(${rotY.toFixed(2)}deg) rotateX(${lerp(8, 3, w).toFixed(2)}deg) scale(${breathe.toFixed(4)})`,
        out * 10,
      );
      style(glow, w * (1 - out) * 0.9, `translateX(${(tx * 0.8).toFixed(1)}px) scaleX(${lerp(0.5, 1, w).toFixed(3)})`);

      let current = 0;
      shots.forEach((shot, i) => {
        if (lt >= shot.start) current = i;
        const reveal = i === 0 ? 1 : tw(lt, shot.start, 0.9);
        const next = shots[i + 1];
        const covered = next ? tw(lt, next.start, 0.9) : 0;
        shot.img.style.clipPath = i === 0 ? 'none' : `inset(${((1 - reveal) * 100).toFixed(2)}% 0 0 0)`;
        shot.img.style.filter = covered > 0.001 ? `brightness(${(1 - covered * 0.5).toFixed(3)})` : 'none';
        if (shot.callout) {
          const at = shot.start + Math.min(2.2, (next ? next.start - shot.start : s.dur - shot.start) * 0.3);
          const until = next ? next.start - 0.1 : s.dur - 0.55;
          const p = tw(lt, at, 1.0, E.spring);
          const gone = tw(lt, until - 0.4, 0.4, E.inCubic);
          style(
            shot.callout,
            clamp((lt - at) / 0.2) * (1 - gone),
            `translate(${(dir * (1 - p) * 60).toFixed(1)}px, ${((1 - p) * 50 - gone * 30).toFixed(1)}px) scale(${lerp(0.7, 1, p).toFixed(4)}) rotate(${(dir * -1.5).toFixed(1)}deg)`,
          );
        }
      });
      const cur = shots[current];
      const typed = Math.floor(tw(lt, cur.start + (current ? 0.1 : 0.4), 0.5, E.linear) * cur.path.length);
      // The previous address stays up until the new one starts typing.
      urlPath.textContent = current && !typed ? shots[current - 1].path : cur.path.slice(0, Math.max(1, typed));
    };
  }

  function buildPlatform(s, root) {
    const title = h('div', 'p-title', root);
    const titleChars = letters(s.title, title, { gradient: true });
    const tag = h('div', 'p-tagline', root);
    const tagWords = words(s.tagline, tag);

    const hero = h('div', 'p-hero', root);
    const light = h('img', null, hero);
    light.src = s.shots.light;
    const dark = h('img', null, hero);
    dark.src = s.shots.dark;
    const labels = [
      ['sun', 'Light theme'],
      ['moon', 'Dark theme'],
    ].map(([ic, label]) => {
      const l = h('div', 'label', hero);
      icon(ic, l);
      h('span', null, l, label);
      return l;
    });
    // Where the theme toggle sits in the dark capture, in card pixels.
    const coverScale = Math.max(920 / T.capture.width, 640 / T.capture.height);
    const toggleX = T.capture.width * 0.88 * coverScale - (T.capture.width * coverScale - 920) / 2;
    const toggleY = 27 * coverScale;

    const colors = T.modules.map(m => m.color);
    const cards = s.cards.map(([ic, heading, body], i) => {
      const card = h('div', 'p-card', root);
      place(card, 1090 + (i % 2) * 380, 320 + Math.floor(i / 2) * 221);
      tile(ic, colors[(i * 2 + 1) % colors.length], card);
      h('h3', null, card, heading);
      h('p', null, card, body);
      return card;
    });

    return lt => {
      const out = tw(lt, s.dur - 0.6, 0.6, E.inCubic);
      root.style.opacity = (1 - out).toFixed(3);
      root.style.transform = `translateY(${(-30 * out).toFixed(1)}px)`;
      riseLetters(titleChars, lt, 0.1, 0.03);
      blurWords(tagWords, lt, 0.5);

      const e = tw(lt, 0.3, 1.2);
      style(hero, e, `translateY(${((1 - e) * 80).toFixed(1)}px) scale(${lerp(0.94, 1, e).toFixed(4)})`);
      const reveal = tw(lt, 3.0, 1.6, E.inOutCubic);
      dark.style.clipPath = `circle(${(reveal * 1300).toFixed(1)}px at ${toggleX.toFixed(1)}px ${toggleY.toFixed(1)}px)`;
      const swap = tw(lt, 3.5, 0.5, E.outCubic);
      style(labels[0], clamp(tw(lt, 1.0, 0.6) - swap), `translateY(${(-20 * swap).toFixed(1)}px)`);
      style(labels[1], swap, `translateY(${((1 - swap) * 20).toFixed(1)}px)`);

      cards.forEach((card, i) => {
        const at = 1.0 + i * BEAT;
        const p = tw(lt, at, 0.7, E.outBack);
        style(card, clamp((lt - at) / 0.3), `translateY(${((1 - p) * 60).toFixed(1)}px) scale(${lerp(0.9, 1, clamp(p)).toFixed(4)})`);
      });
    };
  }

  function buildOutro(s, root) {
    const n = T.modules.length;
    const tiles = T.modules.map(m => tile(m.icon, m.color, root, 'orbit-tile'));
    const logo = tile('users', null, root, 'hero-logo logo-tile');
    logo.style.top = '300px';
    const rings = [0, 1, 2].map(() => h('div', 'ring', logo));
    const title = h('div', 'center-stack hero-title', root);
    title.style.top = '510px';
    const chars = letters(s.title, title, { gradient: true });
    const tag = h('div', 'center-stack hero-tagline', root);
    tag.style.top = '720px';
    const tagWords = words(s.tagline, tag);
    const urlRow = h('div', 'center-stack', root);
    urlRow.style.top = '830px';
    const pill = h('div', 'url-pill', urlRow);
    icon(s.url_icon, pill);
    h('span', null, pill, s.url);

    return lt => {
      tiles.forEach((e, i) => {
        const pop = tw(lt, 0.1 + i * 0.06, 0.9, E.spring);
        const converge = tw(lt, 2.0, 1.0, E.inCubic);
        const angle = (i / n) * Math.PI * 2 + lt * 0.45 + converge * 1.5;
        const radius = 380 * (1 - converge) * lerp(0.6, 1, clamp(pop));
        const x = 960 - 46 + Math.cos(angle) * radius * 1.25;
        const y = 384 - 46 + Math.sin(angle) * radius;
        style(e, clamp(pop * 2) * (1 - tw(lt, 2.7, 0.3, E.linear)), `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(${(clamp(pop) * lerp(1, 0.3, converge)).toFixed(4)})`);
      });
      const k = tw(lt, 2.8, 1.2, E.spring);
      style(logo, clamp(k * 3), `scale(${k.toFixed(4)})`);
      rings.forEach((r, i) => {
        const at = 2.9 + i * 0.22;
        const p = tw(lt, at, 1.4, E.outCubic);
        style(r, lt > at ? (1 - p) * 0.8 : 0, `scale(${lerp(1, 2.8, p).toFixed(3)})`);
      });
      riseLetters(chars, lt, 3.2, 0.045, 0.9);
      blurWords(tagWords, lt, 4.1, 0.12, 0.9);
      const u = tw(lt, 5.3, 1.0);
      style(urlRow, u, `translateY(${((1 - u) * 30).toFixed(1)}px)`);
    };
  }

  function moduleCount() {
    return T.sections.filter(s => s.kind === 'module').length;
  }

  // -- frame --------------------------------------------------------------------

  function sectionAt(t) {
    let found = sections[0];
    for (const s of sections) if (t >= s.start) found = s;
    return found;
  }

  function sceneColor(t) {
    const s = sectionAt(t);
    const next = sections[s.index + 1];
    const prev = sections[s.index - 1];
    if (next && t > next.start - 0.7) return mix(s.rgb, next.rgb, E.inOutCubic(prog(t, next.start - 0.7, 1.4)));
    if (prev && t < s.start + 0.7) return mix(prev.rgb, s.rgb, E.inOutCubic(prog(t, s.start - 0.7, 1.4)));
    return s.rgb;
  }

  function render(t) {
    for (const s of sections) {
      const lt = t - s.start;
      const visible = lt > -0.9 && lt < s.dur + 0.9;
      s.root.style.display = visible ? '' : 'none';
      if (visible) s.update(lt);
    }

    // background
    const s = sectionAt(t);
    const lt = t - s.start;
    const pulse = lt < s.drumEnd ? Math.exp(-(lt % BEAT) / 0.13) : 0;
    const c = sceneColor(t);
    stageEl.style.setProperty('--c', cssRgb(c));
    stageEl.style.setProperty('--c2', cssRgb(mix(c, rgb(T.brand[1]), 0.55)));
    const path = [
      [520, 260, 260, 140, 0.13, 0.17],
      [1480, 820, 240, 160, 0.11, 0.09],
      [1300, 180, 300, 120, 0.07, 0.12],
    ];
    blobs.forEach((b, i) => {
      const [x, y, ax, ay, fx, fy] = path[i];
      b.style.left = `${(x + Math.sin(t * fx * 6.28 + i) * ax).toFixed(1)}px`;
      b.style.top = `${(y + Math.cos(t * fy * 6.28 + i * 2) * ay).toFixed(1)}px`;
      b.style.opacity = (0.8 + 0.2 * pulse).toFixed(3);
      b.style.transform = `scale(${(1 + 0.04 * pulse).toFixed(4)})`;
    });
    floor.style.backgroundPosition = `0 ${((t * 60) % 96).toFixed(1)}px`;
    floor.style.opacity = (0.16 + 0.12 * pulse).toFixed(3);

    // hud: only while the modules play
    const first = moduleSections[0];
    const last = moduleSections[moduleSections.length - 1];
    const hudOn = tw(t, first.start + 0.3, 0.6, E.outCubic) * (1 - tw(t, last.start + last.dur - 0.6, 0.5, E.inCubic));
    hud.style.opacity = hudOn.toFixed(3);
    if (hudOn > 0) {
      const pos = clamp((t - first.start) / first.dur - 0.5, 0, moduleSections.length - 1);
      dots.forEach((d, i) => {
        const active = clamp(1 - Math.abs(pos - i));
        d.style.width = `${(8 + 34 * active).toFixed(1)}px`;
        d.style.background = active > 0.02 ? `rgb(${cssRgb(mix([90, 90, 110], rgb(T.modules[i].color), active))})` : '';
      });
    }

    // a light sweep across every cut into a module, the platform or the outro
    let w = 0;
    for (const sec of sections.slice(1)) {
      const k = prog(t, sec.start - 0.45, 0.9);
      if (k <= 0 || k >= 1) continue;
      const x = lerp(-900, 2100, E.inOutCubic(k));
      const col = cssRgb(sec.rgb);
      const band = wipes[w++];
      band.style.left = `${x.toFixed(1)}px`;
      band.style.opacity = Math.sin(k * Math.PI).toFixed(3);
      band.style.transform = 'skewX(-18deg)';
      band.style.background = `linear-gradient(90deg, transparent, rgb(${col} / .55) 40%, rgb(255 255 255 / .55) 52%, rgb(${col} / .35) 60%, transparent)`;
      if (w === wipes.length) break;
    }
    for (; w < wipes.length; w++) wipes[w].style.opacity = '0';

    // film grain at 24 fps whatever the render rate
    const g = prng(Math.floor(t * 24) + 1);
    grain.style.backgroundPosition = `${Math.floor(g() * 256)}px ${Math.floor(g() * 256)}px`;

    fade.style.opacity = Math.max(1 - prog(t, 0, 0.8), prog(t, T.duration - 2.2, 2.0)).toFixed(3);
  }

  // -- boot -----------------------------------------------------------------------

  lucide.createIcons({ icons: lucide.icons });
  const missingIcons = [...stageEl.querySelectorAll('i[data-lucide]')].map(i => i.dataset.lucide);
  if (missingIcons.length) console.warn('Unknown lucide icons:', missingIcons);

  const api = { ready: false, render, missingIcons, duration: T.duration };
  window.stage = api;
  Promise.all([
    document.fonts.ready,
    ...[...stageEl.querySelectorAll('img')].map(img => img.decode().catch(() => null)),
  ]).then(() => {
    render(0);
    api.ready = true;
  });

  if (!RENDER) startPlayer();

  function startPlayer() {
    const fit = () => {
      const k = Math.min(innerWidth / T.width, innerHeight / T.height);
      stageEl.style.transform = `scale(${k})`;
    };
    fit();
    addEventListener('resize', fit);

    const ui = h('div', 'player-ui paused', document.body);
    const play = h('button', 'play', ui, 'Play');
    const bar = h('div', 'bar', ui);
    const clock = h('span', null, bar, '0:00');
    const track = h('div', 'track', bar);
    const done = h('div', 'done', track);

    const audio = new Audio(window.SOUNDTRACK || '');
    let fallbackStart = null;
    let fallbackAt = Number(params.get('t') || 0);
    audio.currentTime = fallbackAt;
    const now = () => (fallbackStart == null ? (audio.readyState ? audio.currentTime : fallbackAt) : fallbackAt + (performance.now() - fallbackStart) / 1000);
    const playing = () => !audio.paused || fallbackStart != null;

    function toggle() {
      if (playing()) {
        fallbackAt = now();
        fallbackStart = null;
        audio.pause();
        ui.classList.add('paused');
        play.style.display = '';
      } else {
        if (now() >= T.duration) seek(0);
        audio.play().catch(() => (fallbackStart = performance.now()));
        ui.classList.remove('paused');
        play.style.display = 'none';
      }
    }
    function seek(t) {
      t = clamp(t, 0, T.duration);
      fallbackAt = t;
      if (fallbackStart != null) fallbackStart = performance.now();
      audio.currentTime = t;
      render(t);
    }
    play.addEventListener('click', toggle);
    stageEl.addEventListener('click', toggle);
    track.addEventListener('click', e => seek((e.offsetX / track.clientWidth) * T.duration));
    addEventListener('keydown', e => {
      if (e.code === 'Space') { e.preventDefault(); toggle(); }
      if (e.code === 'ArrowRight') seek(now() + (e.shiftKey ? 1 / T.fps : 5));
      if (e.code === 'ArrowLeft') seek(now() - (e.shiftKey ? 1 / T.fps : 5));
      if (e.code === 'Home') seek(0);
    });

    const loop = () => {
      const t = Math.min(now(), T.duration);
      if (playing()) render(t);
      if (t >= T.duration && playing()) toggle();
      clock.textContent = `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')} / ${Math.floor(T.duration / 60)}:${String(Math.round(T.duration % 60)).padStart(2, '0')}`;
      done.style.width = `${(t / T.duration) * 100}%`;
      requestAnimationFrame(loop);
    };
    const wait = setInterval(() => {
      if (api.ready) { clearInterval(wait); render(fallbackAt); loop(); }
    }, 50);
  }
})();
