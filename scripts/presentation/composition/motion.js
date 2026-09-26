/*
 * Motion shared by the presentation's compositions. index.html loads it once;
 * each scene's sub-composition calls window.presentation.scene() from its own
 * inline script to fill its paused timeline, and index.html calls ambient()
 * for what spans the whole video.
 *
 * Scene timelines are local: a scene starts at 0, HyperFrames places it.
 */
(() => {
  'use strict';

  const SPRING = 'elastic.out(1, 0.55)';
  const $ = (root, sel) => root.querySelector(sel);
  const $$ = (root, sel) => [...root.querySelectorAll(sel)];

  function prng(seed) {
    return () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let r = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  function drawIcons() {
    lucide.createIcons({ icons: lucide.icons });
  }

  // -- motion vocabulary ------------------------------------------------------

  function vocabulary(tl) {
    return {
      blurIn(targets, at, { stagger = 0.055, duration = 0.8 } = {}) {
        tl.fromTo(
          targets,
          { opacity: 0, y: 26, filter: 'blur(10px)' },
          { opacity: 1, y: 0, filter: 'blur(0px)', duration, stagger, ease: 'power3.out' },
          at,
        );
      },
      rise(chars, at, stagger = 0.035, duration = 0.75) {
        tl.fromTo(chars, { yPercent: 105, opacity: 0 }, { yPercent: 0, opacity: 1, duration, stagger, ease: 'expo.out' }, at);
      },
      pop(el, at, rotation = 0, duration = 1.0) {
        tl.fromTo(el, { scale: 0, opacity: 0, rotation }, { scale: 1, opacity: 1, rotation: 0, duration, ease: SPRING }, at);
      },
      ring(el, at, scale, duration) {
        tl.fromTo(el, { scale: 1, opacity: 0.85 }, { scale, opacity: 0, duration, ease: 'power3.out' }, at);
      },
    };
  }

  // -- scenes -------------------------------------------------------------------

  function intro(tl, el, s, D) {
    const { blurIn, pop, ring } = vocabulary(tl);
    const flare = $(el, '.flare');
    tl.fromTo(flare, { scaleX: 0.02, opacity: 0 }, { scaleX: 1, opacity: 1, duration: 1.2, ease: 'power3.out' }, 0.2);
    tl.to(flare, { scaleY: 0.3, opacity: 0, duration: 0.8, ease: 'power3.out' }, 1.5);

    const logo = $(el, '.hero-logo');
    pop(logo, 0.6, -120, 1.3);
    $$(logo, '.ring').forEach((r, i) => ring(r, 1.15 + i * 0.25, 2.4, 1.3));

    tl.fromTo(
      $$(el, '.hero-title .ch'),
      { y: 70, scale: 1.3, opacity: 0, filter: 'blur(16px)' },
      { y: 0, scale: 1, opacity: 1, filter: 'blur(0px)', duration: 1.1, stagger: 0.06, ease: 'expo.out' },
      1.5,
    );
    blurIn($$(el, '.hero-tagline .w'), 2.5, { stagger: 0.08, duration: 0.9 });
    tl.to($(el, '.hero-tagline'), { opacity: 0, duration: 0.5, ease: 'power3.out' }, 3.7);
    tl.to($(el, '.intro-group'), { y: -190, scale: 0.82, duration: 1.1, ease: 'power3.inOut' }, 3.8);
    blurIn($$(el, '.hero-sub .w'), 5.1, { stagger: 0.05 });

    const rand = prng(7);
    $$(el, '.orbit-tile').forEach((tile, i) => {
      const angle = rand() * Math.PI * 2;
      tl.fromTo(
        tile,
        { x: Math.cos(angle) * 1300, y: Math.sin(angle) * 900, rotation: (rand() - 0.5) * 540, scale: 0.4, opacity: 0 },
        { x: 0, y: 0, rotation: 0, scale: 1, opacity: 1, duration: 1.2, ease: SPRING },
        4.1 + i * 0.07,
      );
      // Everything flies past the camera into the first scene.
      const cx = parseFloat(tile.style.left) + 46 - 960;
      tl.to(tile, { x: cx * 0.9, y: 220, scale: 1.8, opacity: 0, filter: 'blur(10px)', duration: 0.8, ease: 'power3.in' }, D - 0.8);
    });
    tl.to($(el, '.intro-exit'), { scale: 1.6, opacity: 0, filter: 'blur(16px)', duration: 0.8, ease: 'power3.in' }, D - 0.8);
    tl.to($(el, '.hero-sub'), { opacity: 0, duration: 0.8, ease: 'power3.in' }, D - 0.8);
  }

  function module(tl, el, s, D, BEAT) {
    const { blurIn, rise, pop, ring } = vocabulary(tl);
    const dir = s.number % 2 === 1 ? 1 : -1;
    const out = D - 0.55;

    // text column
    tl.fromTo($(el, '.m-counter'), { x: -30, opacity: 0 }, { x: 0, opacity: 1, duration: 0.7, ease: 'expo.out' }, 0);
    pop($(el, '.m-icon .tile'), 0.05, -30);
    ring($(el, '.m-icon .ring'), 0.5, 1.9, 1.0);
    rise($$(el, '.m-name .ch'), 0.18);
    blurIn($$(el, '.m-tagline .w'), 0.7);
    $$(el, '.feat').forEach((feat, i) => {
      const at = 1.5 + i * BEAT;
      tl.fromTo(feat, { x: -44, scale: 0.92, opacity: 0 }, { x: 0, scale: 1, opacity: 1, duration: 0.65, ease: 'back.out(1.7)' }, at);
      tl.fromTo($(feat, '.flash'), { opacity: 0.9, scale: 1 }, { opacity: 0, scale: 1.5, duration: 0.7, ease: 'power2.out' }, at);
    });
    tl.to($(el, '.m-text'), { y: -40, opacity: 0, filter: 'blur(8px)', duration: 0.55, ease: 'power3.in' }, out);

    // the window enters from its own side and leaves the same way
    const win = $(el, '.m-window');
    tl.fromTo(
      win,
      { x: dir * 560, z: -380, rotationY: dir * -40, rotationX: 8, opacity: 0 },
      { x: 0, z: 0, rotationY: dir * -13, rotationX: 3, opacity: 1, duration: 1.5, ease: 'expo.out' },
      0.1,
    );
    tl.to(win, { rotationY: dir * -8, duration: out - 1.6, ease: 'sine.inOut' }, 1.6);
    tl.fromTo(win, { y: -6 }, { y: 6, duration: (D - 0.1) / 2, yoyo: true, repeat: 1, ease: 'sine.inOut' }, 0.1);
    // The page is never zoomed or cropped: the whole window breathes instead.
    tl.fromTo(win, { scale: 1 }, { scale: 1.03, duration: out - 0.8, ease: 'sine.inOut' }, 0.8);
    tl.to(win, { x: dir * 460, z: -650, rotationY: dir * -30, opacity: 0, filter: 'blur(10px)', duration: 0.55, ease: 'power3.in' }, out);

    const glow = $(el, '.m-glow');
    tl.fromTo(glow, { x: dir * 450, scaleX: 0.5, opacity: 0 }, { x: 0, scaleX: 1, opacity: 0.9, duration: 1.5, ease: 'expo.out' }, 0.1);
    tl.to(glow, { opacity: 0, duration: 0.55, ease: 'power3.in' }, out);

    // shots: each one wipes up over the previous, its address typed in
    const shots = $$(el, '.screen img');
    const paths = $$(el, '.url .p');
    const shotDur = D / shots.length;
    shots.forEach((img, k) => {
      const start = k * shotDur;
      const typeAt = start + (k ? 0.1 : 0.4);
      if (k) {
        tl.fromTo(img, { clipPath: 'inset(100% 0% 0% 0%)' }, { clipPath: 'inset(0% 0% 0% 0%)', duration: 0.9, ease: 'expo.out' }, start);
        tl.to(shots[k - 1], { filter: 'brightness(0.5)', duration: 0.9, ease: 'expo.out' }, start);
        // the previous address stays up until the new one starts typing
        tl.set(paths[k - 1], { display: 'none' }, typeAt);
      }
      tl.set(paths[k], { display: 'inline' }, start);
      const chars = $$(paths[k], '.c');
      chars.forEach((c, j) => tl.set(c, { display: 'inline' }, typeAt + (j * 0.5) / chars.length));
    });

    $$(el, '.callout').forEach((callout) => {
      const k = Number(callout.dataset.shot);
      const start = k * shotDur;
      const end = k < shots.length - 1 ? start + shotDur - 0.1 : out;
      const at = start + Math.min(2.2, (end - start) * 0.3);
      tl.fromTo(
        callout,
        { x: dir * 60, y: 50, scale: 0.7, opacity: 0, rotation: dir * -1.5 },
        { x: 0, y: 0, scale: 1, opacity: 1, rotation: dir * -1.5, duration: 1.0, ease: SPRING },
        at,
      );
      tl.to(callout, { y: -30, opacity: 0, duration: 0.4, ease: 'power3.in' }, end - 0.4);
    });
  }

  function platform(tl, el, s, D, BEAT, T) {
    const { blurIn, rise } = vocabulary(tl);
    rise($$(el, '.p-title .ch'), 0.1, 0.03);
    blurIn($$(el, '.p-tagline .w'), 0.5);

    tl.fromTo($(el, '.p-hero'), { y: 80, scale: 0.94, opacity: 0 }, { y: 0, scale: 1, opacity: 1, duration: 1.2, ease: 'expo.out' }, 0.3);
    // The dark theme spreads from the theme toggle of the dark capture.
    const cover = Math.max(920 / T.capture.width, 640 / T.capture.height);
    const tx = T.capture.width * 0.88 * cover - (T.capture.width * cover - 920) / 2;
    const ty = 27 * cover;
    tl.fromTo(
      $(el, '.p-hero .dark'),
      { clipPath: `circle(0px at ${tx.toFixed(1)}px ${ty.toFixed(1)}px)` },
      { clipPath: `circle(1300px at ${tx.toFixed(1)}px ${ty.toFixed(1)}px)`, duration: 1.6, ease: 'power2.inOut' },
      3.0,
    );
    const light = $(el, '.label.light');
    const dark = $(el, '.label.dark');
    tl.fromTo(light, { y: 20, opacity: 0 }, { y: 0, opacity: 1, duration: 0.6, ease: 'expo.out' }, 1.0);
    tl.to(light, { y: -20, opacity: 0, duration: 0.5, ease: 'power3.out' }, 3.5);
    tl.fromTo(dark, { y: 20, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: 'power3.out' }, 3.5);

    $$(el, '.p-card').forEach((card, i) => {
      tl.fromTo(card, { y: 60, scale: 0.9, opacity: 0 }, { y: 0, scale: 1, opacity: 1, duration: 0.7, ease: 'back.out(1.7)' }, 1.0 + i * BEAT);
    });
    tl.to($(el, '.p-inner'), { y: -30, opacity: 0, duration: 0.6, ease: 'power3.in' }, D - 0.6);
  }

  function outro(tl, el) {
    const { blurIn, rise, pop, ring } = vocabulary(tl);
    const arms = $$(el, '.arm');
    arms.forEach((arm, i) => {
      const base = (i / arms.length) * 360;
      const tile = $(arm, '.orbit-tile');
      const at = 0.1 + i * 0.06;
      // The arm turns and the tile turns back by as much, so icons stay upright.
      tl.fromTo(arm, { rotation: base }, { rotation: base + 160, duration: 3.0, ease: 'power1.in' }, 0);
      tl.fromTo(tile, { rotation: -base }, { rotation: -base - 160, duration: 3.0, ease: 'power1.in' }, 0);
      tl.fromTo(tile, { x: 230, scale: 0, opacity: 0 }, { x: 380, scale: 1, opacity: 1, duration: 0.9, ease: SPRING }, at);
      tl.to(tile, { x: 0, scale: 0.3, duration: 1.0, ease: 'power3.in' }, 2.0);
      tl.to(tile, { opacity: 0, duration: 0.3, ease: 'none' }, 2.7);
    });
    const logo = $(el, '.hero-logo');
    pop(logo, 2.8, 0, 1.2);
    $$(logo, '.ring').forEach((r, i) => ring(r, 2.9 + i * 0.22, 2.8, 1.4));
    rise($$(el, '.hero-title .ch'), 3.2, 0.045, 0.9);
    blurIn($$(el, '.hero-tagline .w'), 4.1, { stagger: 0.12, duration: 0.9 });
    tl.fromTo($(el, '.url-row'), { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 1.0, ease: 'expo.out' }, 5.3);
  }

  const BUILDERS = { intro, module, platform, outro };

  /** Fill a scene's own timeline; *index* is its place in TIMELINE.sections. */
  function scene(tl, el, index) {
    const T = window.TIMELINE;
    const beat = 60 / T.bpm;
    const s = T.sections[index];
    drawIcons();
    BUILDERS[s.kind](tl, el, s, s.bars * beat * T.beatsPerBar, beat, T);
  }

  // -- what spans the whole video -------------------------------------------------

  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const mix = (a, b, k) => a.map((v, i) => Math.round(v + (b[i] - v) * k));
  const rgb = (c) => `rgb(${c.join(' ')})`;
  const clamp01 = (v) => Math.min(1, Math.max(0, v));

  function timing() {
    const T = window.TIMELINE;
    const BEAT = 60 / T.bpm;
    const BAR = BEAT * T.beatsPerBar;
    const sections = T.sections.map((s) => ({ ...s, dur: s.bars * BAR, rgb: hex(s.color) }));
    const modules = sections.filter((s) => s.kind === 'module');
    return { T, BEAT, BAR, sections, modules };
  }

  // Paint a function of time from one tween, so the result never depends on
  // the order in which the renderer seeks. *offset* turns local time global.
  function everyFrame(tl, duration, paint, offset = 0) {
    const clock = { t: 0 };
    tl.to(clock, { t: duration, duration, ease: 'none', onUpdate: () => paint(offset + clock.t) }, 0);
    paint(offset);
  }

  /** A light sweep across every cut. Its slot spans the whole video. */
  function cuts(tl, el) {
    const { sections } = timing();
    sections.slice(1).forEach((s) => {
      const wipe = $(el, `[data-cut="${s.key}"]`);
      const at = s.start - 0.45;
      tl.fromTo(wipe, { x: -900 }, { x: 2100, duration: 0.9, ease: 'power2.inOut' }, at);
      tl.fromTo(wipe, { opacity: 0 }, { opacity: 1, duration: 0.45, ease: 'sine.out' }, at);
      tl.to(wipe, { opacity: 0, duration: 0.45, ease: 'sine.in' }, at + 0.45);
    });
  }

  /** Logo and progress dots while the modules play. Its slot starts with them. */
  function hud(tl, el) {
    const { T, modules } = timing();
    drawIcons();
    const first = modules[0];
    const last = modules[modules.length - 1];
    const span = last.start + last.dur - first.start;
    const inner = $(el, '.hud-inner');
    tl.fromTo(inner, { opacity: 0 }, { opacity: 1, duration: 0.6, ease: 'power3.out' }, 0.3);
    tl.to(inner, { opacity: 0, duration: 0.5, ease: 'power3.in' }, span - 0.6);

    const dots = $$(el, '.hud-dot');
    everyFrame(tl, span, (t) => {
      const pos = Math.min(Math.max((t - first.start) / first.dur - 0.5, 0), modules.length - 1);
      dots.forEach((d, k) => {
        const active = clamp01(1 - Math.abs(pos - k));
        d.style.width = `${(8 + 34 * active).toFixed(1)}px`;
        d.style.background = active > 0.02 ? rgb(mix([90, 90, 110], hex(T.modules[k].color), active)) : '';
      });
    }, first.start);
  }

  /** The backdrop behind every scene and the fades at both ends. */
  function ambient(tl) {
    const { T, BEAT, BAR, sections } = timing();

    const fade = $(document, '#fade');
    tl.fromTo(fade, { opacity: 1 }, { opacity: 0, duration: 0.8, ease: 'none' }, 0);
    tl.to(fade, { opacity: 1, duration: 2.0, ease: 'none' }, T.duration - 2.2);

    const html = document.documentElement;
    const blobs = $$(document, '.blob');
    const floor = $(document, '.floor');
    const grain = $(document, '.grain');
    const inOut = (k) => (k < 0.5 ? 4 * k ** 3 : 1 - (-2 * k + 2) ** 3 / 2);
    const brandTo = hex(T.brand[1]);
    const drift = [
      [520, 260, 260, 140, 0.13, 0.17],
      [1480, 820, 240, 160, 0.11, 0.09],
      [1300, 180, 300, 120, 0.07, 0.12],
    ];

    function sceneColor(t, i) {
      const s = sections[i];
      const next = sections[i + 1];
      const prev = sections[i - 1];
      if (next && t > next.start - 0.7) return mix(s.rgb, next.rgb, inOut(clamp01((t - next.start + 0.7) / 1.4)));
      if (prev && t < s.start + 0.7) return mix(prev.rgb, s.rgb, inOut(clamp01((t - s.start + 0.7) / 1.4)));
      return s.rgb;
    }

    everyFrame(tl, T.duration, (t) => {
      let i = 0;
      sections.forEach((s, j) => { if (t >= s.start) i = j; });
      const lt = t - sections[i].start;
      const pulse = lt < (sections[i].drumBars || 0) * BAR ? Math.exp(-(lt % BEAT) / 0.13) : 0;

      const c = sceneColor(t, i);
      html.style.setProperty('--c', rgb(c));
      html.style.setProperty('--c2', rgb(mix(c, brandTo, 0.55)));
      blobs.forEach((b, k) => {
        const [x, y, ax, ay, fx, fy] = drift[k];
        b.style.left = `${(x + Math.sin(t * fx * 6.28 + k) * ax).toFixed(1)}px`;
        b.style.top = `${(y + Math.cos(t * fy * 6.28 + k * 2) * ay).toFixed(1)}px`;
        b.style.opacity = (0.8 + 0.2 * pulse).toFixed(3);
        b.style.scale = (1 + 0.04 * pulse).toFixed(4);
      });
      floor.style.backgroundPosition = `0 ${((t * 60) % 96).toFixed(1)}px`;
      floor.style.opacity = (0.16 + 0.12 * pulse).toFixed(3);

      // film grain at 24 fps whatever the render rate
      const g = prng(Math.floor(t * 24) + 1);
      grain.style.backgroundPosition = `${Math.floor(g() * 256)}px ${Math.floor(g() * 256)}px`;
    });
  }

  window.presentation = { scene, hud, cuts, ambient };
})();
