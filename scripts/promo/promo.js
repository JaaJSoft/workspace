/*
 * Motion shared by the promo. Scenes call window.promo.* from their own
 * inline script to fill their own paused timeline (local time: 0 is when the
 * scene's slot opens, `lead` seconds before its cut); index.html calls
 * stage() for the backdrop and every cut between scenes.
 *
 * Scenes only ENTER: the cut is the exit (hyperframes-animation transitions
 * overview), except the closing scene, which fades out.
 */
(() => {
  'use strict';

  // window.PROMO is set by an inline script of index.html, which the
  // compiler runs after this file: read it on first use, never at load.
  let P, BEAT, BAR, PORTRAIT;
  function load() {
    if (P) return;
    P = window.PROMO;
    BEAT = 60 / P.bpm;
    BAR = BEAT * 4;
    PORTRAIT = P.orientation === 'portrait';
  }
  // Where the opening piles its app icons, [x, y, rotation]: the hook
  // builds the pile, the intro pulls the same icons out of the same spots.
  const PILE = {
    landscape: [[380, 290, -12], [700, 190, 9], [1210, 200, -7], [1540, 320, 13], [300, 700, 11], [1640, 720, -9], [640, 860, 5], [1260, 870, -13], [1790, 470, 3]],
    portrait: [[250, 420, -12], [560, 300, 9], [860, 450, -7], [220, 1440, 11], [540, 1560, -9], [860, 1420, 13], [240, 760, 5], [840, 1160, -13], [540, 620, 3]],
  };

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

  /** What a scene needs: its root, timing and the motion vocabulary. */
  function scene(id) {
    load();
    lucide.createIcons({ icons: lucide.icons });
    const data = P.scenes.find((s) => s.id === id);
    const el = document.querySelector(`[data-composition-id="${id}"]`);
    // `at(beats)` is a time on the beat grid, counted from the cut.
    const at = (beats) => data.lead + beats * BEAT;
    const pile = PILE[PORTRAIT ? 'portrait' : 'landscape'];
    return { el, $: (s) => $(el, s), $$: (s) => $$(el, s), at, BEAT, BAR, PORTRAIT, pile, lead: data.lead, end: data.lead + data.duration };
  }

  // -- vocabulary (kinetic-beat-slam: one beat grid, a distinct entrance per phrase)

  const SLAMS = {
    scale: [{ scale: 1.6, opacity: 0, filter: 'blur(14px)' }, { scale: 1, opacity: 1, filter: 'blur(0px)', duration: 0.45, ease: 'power4.out' }],
    left: [{ x: -260, opacity: 0 }, { x: 0, opacity: 1, duration: 0.4, ease: 'expo.out' }],
    right: [{ x: 260, opacity: 0 }, { x: 0, opacity: 1, duration: 0.4, ease: 'expo.out' }],
    rise: [{ y: 110, rotation: 5, opacity: 0 }, { y: 0, rotation: 0, opacity: 1, duration: 0.5, ease: 'circ.out' }],
    drop: [{ y: -130, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: 'back.out(2)' }],
  };

  function slam(tl, target, time, kind = 'scale') {
    const [from, to] = SLAMS[kind];
    tl.fromTo(target, { ...from }, { ...to }, time);
  }

  // spring-pop-entrance, overshoot register
  function pop(tl, target, time, { rotation = 0, duration = 0.55, stagger = 0 } = {}) {
    tl.fromTo(target, { scale: 0, opacity: 0, rotation }, { scale: 1, opacity: 1, rotation: 0, duration, stagger, ease: 'back.out(2.2)' }, time);
  }

  // A take's window: flies in tilted and settles (cursor-ui-demo surface).
  function windowIn(tl, target, time, side = 1) {
    tl.fromTo(
      target,
      { x: side * 180, rotationY: side * -24, scale: 0.86, opacity: 0 },
      { x: 0, rotationY: side * -8, scale: 1, opacity: 1, duration: 0.7, ease: 'expo.out' },
      time,
    );
  }

  // sine-wave-loop, finite: floor, never ceil, never -1
  function drift(tl, target, from, until, props, cycle = 2.4) {
    const span = Math.max(0, until - from);
    tl.to(target, { ...props, duration: cycle / 2, ease: 'sine.inOut', yoyo: true, repeat: Math.max(0, Math.floor(span / (cycle / 2)) - 1) }, from);
  }

  // -- the root: backdrop, cuts, fades ------------------------------------------------

  function stage(tl) {
    load();
    lucide.createIcons({ icons: lucide.icons });
    const hosts = Object.fromEntries(P.scenes.map((s) => [s.id, document.getElementById(s.id)]));
    P.scenes.forEach((s, i) => {
      if (i) cut(tl, s.cut, hosts[P.scenes[i - 1].id], hosts[s.id], s.start);
    });

    const fade = document.getElementById('fade');
    // An ad opens on its first frame: no fade in.
    tl.set(fade, { opacity: 0 }, 0);
    tl.to(fade, { opacity: 1, duration: 1.4, ease: 'none' }, P.duration - 1.5);

    // Ambient layer painted from time, so it never depends on seek order:
    // drifting glows and ghost type, and a pulse on every kick.
    const glows = $$(document, '.glow');
    const ghost = $(document, '.ghost');
    const grid = $(document, '.grid');
    const drums = P.sections.map((s) => [s.start, s.start + s.drumBars * BAR]);
    const paint = (t) => {
      let pulse = 0;
      for (const [a, b] of drums) if (t >= a && t < b) pulse = Math.exp(-((t - a) % BEAT) / 0.12);
      glows.forEach((g, k) => {
        g.style.translate = `${(Math.sin(t * 0.35 + k * 2) * 160).toFixed(1)}px ${(Math.cos(t * 0.27 + k) * 110).toFixed(1)}px`;
        g.style.scale = (1 + 0.08 * pulse).toFixed(4);
        g.style.opacity = (0.75 + 0.25 * pulse).toFixed(3);
      });
      ghost.style.translate = `${(-((t * 40) % 1600)).toFixed(1)}px 0`;
      // The brand stays out of the opening; it is the intro's reveal.
      ghost.style.opacity = Math.min(1, Math.max(0, (t - P.scenes[2].start) / 0.6)).toFixed(3);
      grid.style.opacity = (0.5 + 0.5 * pulse).toFixed(3);
    };
    const clock = { t: 0 };
    tl.to(clock, { t: P.duration, duration: P.duration, ease: 'none', onUpdate: () => paint(clock.t) }, 0);
    paint(0);
  }

  // Cuts between scene slots, per hyperframes-animation transitions: the
  // outgoing and incoming slots move at the same moment T, the move is the
  // handoff. High energy: 0.25-0.4s, power4/expo.
  function cut(tl, kind, out, next, T) {
    switch (kind) {
      case 'zoom': // zoom-through, the primary
        tl.to(out, { scale: 1.45, opacity: 0, filter: 'blur(18px)', duration: 0.3, ease: 'power3.in' }, T - 0.3);
        tl.fromTo(next, { scale: 0.72, opacity: 0, filter: 'blur(18px)' }, { scale: 1, opacity: 1, filter: 'blur(0px)', duration: 0.45, ease: 'expo.out' }, T - 0.12);
        break;
      case 'zoom-back': // inverse zoom-through: an arrival
        tl.to(out, { scale: 0.8, opacity: 0, filter: 'blur(18px)', duration: 0.3, ease: 'power3.in' }, T - 0.3);
        tl.fromTo(next, { scale: 1.3, opacity: 0, filter: 'blur(18px)' }, { scale: 1, opacity: 1, filter: 'blur(0px)', duration: 0.5, ease: 'expo.out' }, T - 0.12);
        break;
      case 'whip-left':
      case 'whip-right': { // cut-the-curve: same direction on both sides, mirrored eases
        const d = kind === 'whip-left' ? -1 : 1;
        tl.to(out, { xPercent: d * 28, filter: 'blur(18px)', duration: 0.3, ease: 'power4.in' }, T - 0.3);
        tl.to(out, { opacity: 0, duration: 0.22, ease: 'power1.in' }, T - 0.24);
        tl.fromTo(next, { xPercent: -d * 28, opacity: 0.35, filter: 'blur(18px)' }, { xPercent: 0, opacity: 1, filter: 'blur(0px)', duration: 0.34, ease: 'power4.out' }, T);
        tl.set(next, { opacity: 0 }, T - 0.3);
        break;
      }
      case 'flash': { // overexposure: the drop
        const flash = document.getElementById('flash');
        tl.to(out, { scale: 1.12, filter: 'brightness(2.2) blur(6px)', duration: 0.2, ease: 'power2.in' }, T - 0.2);
        tl.fromTo(flash, { opacity: 0 }, { opacity: 1, duration: 0.14, ease: 'power2.in' }, T - 0.14);
        tl.set(out, { opacity: 0 }, T);
        tl.fromTo(next, { opacity: 0 }, { opacity: 1, duration: 0.01 }, T);
        tl.fromTo(next, { scale: 1.08, filter: 'brightness(1.8)' }, { scale: 1, filter: 'brightness(1)', duration: 0.6, ease: 'expo.out' }, T);
        tl.to(flash, { opacity: 0, duration: 0.45, ease: 'expo.out' }, T);
        break;
      }
      case 'blocks': { // staggered colour blocks: a change of section
        const blocks = $$(document, '#cover .block');
        tl.set(next, { opacity: 0 }, T - 0.3);
        blocks.forEach((b, i) => {
          tl.fromTo(b, { xPercent: -100 }, { xPercent: 0, duration: 0.24, ease: 'power3.inOut' }, T - 0.3 + i * 0.05);
          tl.to(b, { xPercent: 100, duration: 0.24, ease: 'power3.inOut' }, T + 0.02 + i * 0.05);
        });
        tl.set(out, { opacity: 0 }, T - 0.02);
        tl.set(next, { opacity: 1 }, T - 0.02);
        break;
      }
      case 'blur': // blur crossfade: the wind-down into the breakdown
        tl.to(out, { opacity: 0, filter: 'blur(24px)', duration: 0.6, ease: 'sine.inOut' }, T - 0.3);
        tl.fromTo(next, { opacity: 0, filter: 'blur(24px)' }, { opacity: 1, filter: 'blur(0px)', duration: 0.6, ease: 'sine.inOut' }, T - 0.3);
        break;
      case 'hard': // a straight cut on the beat, when the motion itself carries across
        tl.set(next, { opacity: 0 }, T - 0.3);
        tl.set(out, { opacity: 0 }, T);
        tl.set(next, { opacity: 1 }, T);
        break;
      default:
        tl.fromTo(next, { opacity: 0 }, { opacity: 1, duration: 0.01 }, T);
    }
  }

  window.promo = { scene, slam, pop, windowIn, drift, prng, stage };
})();
