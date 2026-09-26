/*
 * Motion shared by the promo. Scenes call window.promo.* from their own
 * inline script to fill their own paused timeline (local time: 0 is when the
 * scene's slot opens, `lead` seconds before its cut); index.html calls
 * stage() for the backdrop and every cut between scenes.
 *
 * Everything is flat and full screen: cuts cover or reveal the whole frame,
 * nothing tilts, nothing casts a shadow. Scenes only enter; the cut is the
 * exit, except for the closing scene.
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

  const $ = (root, sel) => root.querySelector(sel);
  const $$ = (root, sel) => [...root.querySelectorAll(sel)];

  /** What a scene needs: its root, timing and the motion vocabulary. */
  function scene(id) {
    load();
    lucide.createIcons({ icons: lucide.icons });
    const data = P.scenes.find((s) => s.id === id);
    const el = document.querySelector(`[data-composition-id="${id}"]`);
    // `at(beats)` is a time on the beat grid, counted from the cut.
    const at = (beats) => data.lead + beats * BEAT;
    return { el, $: (s) => $(el, s), $$: (s) => $$(el, s), at, BEAT, BAR, PORTRAIT, lead: data.lead, end: data.lead + data.duration };
  }

  // -- vocabulary (kinetic-beat-slam: one beat grid, a distinct entrance per phrase)

  const SLAMS = {
    scale: [{ scale: 1.6, opacity: 0, filter: 'blur(12px)' }, { scale: 1, opacity: 1, filter: 'blur(0px)', duration: 0.4, ease: 'power4.out' }],
    left: [{ xPercent: -30, opacity: 0 }, { xPercent: 0, opacity: 1, duration: 0.35, ease: 'expo.out' }],
    right: [{ xPercent: 30, opacity: 0 }, { xPercent: 0, opacity: 1, duration: 0.35, ease: 'expo.out' }],
    rise: [{ yPercent: 60, opacity: 0 }, { yPercent: 0, opacity: 1, duration: 0.4, ease: 'expo.out' }],
    drop: [{ yPercent: -60, opacity: 0 }, { yPercent: 0, opacity: 1, duration: 0.45, ease: 'back.out(2)' }],
  };

  function slam(tl, target, time, kind = 'scale') {
    const [from, to] = SLAMS[kind];
    tl.fromTo(target, { ...from }, { ...to }, time);
  }

  // spring-pop-entrance, overshoot register
  function pop(tl, target, time, { rotation = 0, duration = 0.45 } = {}) {
    tl.fromTo(target, { scale: 0, opacity: 0, rotation }, { scale: 1, opacity: 1, rotation: 0, duration, ease: 'back.out(2.2)' }, time);
  }

  /*
   * A module's beat: the title card slams on the cut, then on the beat the
   * card is pushed off the top and the take comes up full screen under it.
   */
  function feature(tl, s, cardBeats) {
    pop(tl, s.$('.card-icon'), s.at(0), { rotation: -20, duration: 0.4 });
    const lines = s.$$('.card-title .ln');
    slam(tl, lines[0], s.at(0), 'scale');
    if (lines[1]) slam(tl, lines[1], s.at(Math.min(1, cardBeats / 2)), 'rise');

    const T = s.at(cardBeats);
    tl.to(s.$('.card'), { yPercent: -100, duration: 0.3, ease: 'power4.inOut' }, T - 0.15);
    tl.fromTo(s.$('.shot'), { yPercent: 100 }, { yPercent: 0, duration: 0.3, ease: 'power4.inOut' }, T - 0.15);
    tl.fromTo(s.$('.clip-frame'), { scale: 1.06 }, { scale: 1, duration: 0.6, ease: 'expo.out' }, T);
    slam(tl, s.$('.tag'), T + 0.1, 'left');
  }

  // -- the root: backdrop, cuts, fades ------------------------------------------------

  function stage(tl) {
    load();
    lucide.createIcons({ icons: lucide.icons });
    const hosts = Object.fromEntries(P.scenes.map((s) => [s.id, document.getElementById(s.id)]));
    P.scenes.forEach((s, i) => {
      if (i) cut(tl, s, hosts[P.scenes[i - 1].id], hosts[s.id], s.start);
    });

    const fade = document.getElementById('fade');
    // An ad opens on its first frame: no fade in.
    tl.set(fade, { opacity: 0 }, 0);
    tl.to(fade, { opacity: 1, duration: 1.4, ease: 'none' }, P.duration - 1.5);

    // Ambient glows under the dark scenes, pulsing on every kick.
    const glows = $$(document, '.glow');
    const drums = P.sections.map((s) => [s.start, s.start + s.drumBars * BAR]);
    const paint = (t) => {
      let pulse = 0;
      for (const [a, b] of drums) if (t >= a && t < b) pulse = Math.exp(-((t - a) % BEAT) / 0.12);
      glows.forEach((g, k) => {
        g.style.translate = `${(Math.sin(t * 0.35 + k * 2) * 160).toFixed(1)}px ${(Math.cos(t * 0.27 + k) * 110).toFixed(1)}px`;
        g.style.scale = (1 + 0.08 * pulse).toFixed(4);
        g.style.opacity = (0.75 + 0.25 * pulse).toFixed(3);
      });
    };
    const clock = { t: 0 };
    tl.to(clock, { t: P.duration, duration: P.duration, ease: 'none', onUpdate: () => paint(clock.t) }, 0);
    paint(0);
  }

  // Full-screen cuts between scene slots, per hyperframes-animation
  // transitions: both sides move at the same moment T, high-energy timings.
  function cut(tl, s, out, next, T) {
    switch (s.cut) {
      case 'iris': // circle iris: the next colour opens from the centre
        tl.fromTo(next, { clipPath: 'circle(0% at 50% 50%)' }, { clipPath: 'circle(75% at 50% 50%)', duration: 0.42, ease: 'power3.inOut' }, T - 0.28);
        tl.set(out, { opacity: 0 }, T + 0.15);
        break;
      case 'split': // diagonal split: the next colour sweeps in on a slant
        tl.fromTo(next, { clipPath: 'polygon(0% 0%, 0% 0%, -40% 100%, -40% 100%)' }, { clipPath: 'polygon(0% 0%, 140% 0%, 100% 100%, -40% 100%)', duration: 0.4, ease: 'power3.inOut' }, T - 0.26);
        tl.set(out, { opacity: 0 }, T + 0.15);
        break;
      case 'push-left':
      case 'push-up': { // push: the whole frame is shoved out by the next one
        const prop = s.cut === 'push-left' ? 'xPercent' : 'yPercent';
        tl.to(out, { [prop]: -100, duration: 0.34, ease: 'power4.inOut' }, T - 0.2);
        tl.fromTo(next, { [prop]: 100 }, { [prop]: 0, duration: 0.34, ease: 'power4.inOut' }, T - 0.2);
        break;
      }
      case 'blocks': { // staggered colour blocks in the next scene's colour
        const blocks = $$(document, '#cover .block');
        tl.set(next, { opacity: 0 }, T - 0.3);
        blocks.forEach((b, i) => {
          if (!b.classList.contains('b')) tl.set(b, { backgroundColor: s.color }, T - 0.31);
          tl.fromTo(b, { xPercent: -100 }, { xPercent: 0, duration: 0.22, ease: 'power3.inOut' }, T - 0.3 + i * 0.05);
          tl.to(b, { xPercent: 100, duration: 0.22, ease: 'power3.inOut' }, T + 0.02 + i * 0.05);
        });
        tl.set(out, { opacity: 0 }, T - 0.02);
        tl.set(next, { opacity: 1 }, T - 0.02);
        break;
      }
      case 'flash': { // overexposure: the drop
        const flash = document.getElementById('flash');
        tl.fromTo(flash, { opacity: 0 }, { opacity: 1, duration: 0.14, ease: 'power2.in' }, T - 0.14);
        tl.set(out, { opacity: 0 }, T);
        tl.fromTo(next, { opacity: 0 }, { opacity: 1, duration: 0.01 }, T);
        tl.to(flash, { opacity: 0, duration: 0.5, ease: 'expo.out' }, T);
        break;
      }
      default: // a straight cut on the beat
        tl.set(next, { opacity: 0 }, T - 0.3);
        tl.set(out, { opacity: 0 }, T);
        tl.set(next, { opacity: 1 }, T);
    }
  }

  window.promo = { scene, slam, pop, feature, stage };
})();
