// Shared poll utilities — used by calendar.js, shared.html, and pollDetail
window.pollUtils = {
  voteClass(choice) {
    if (choice === 'yes') return 'bg-success/20 text-success';
    if (choice === 'maybe') return 'bg-warning/20 text-warning';
    if (choice === 'no') return 'bg-error/20 text-error';
    return 'bg-base-200 text-base-content/20';
  },

  voteIcon(choice) {
    if (choice === 'yes') return 'check';
    if (choice === 'maybe') return 'help-circle';
    if (choice === 'no') return 'x';
    return 'circle';
  },

  chosenSlot(poll) {
    if (!poll?.chosen_slot_id) return null;
    return poll.slots.find(s => s.uuid === poll.chosen_slot_id) || null;
  },

  // Every voter counts once, the visitor's own saved votes included: those are
  // the ones flagged is_mine, which the shared page renders in its editable row
  // rather than among the other participants.
  participantCount(poll) {
    return new Set((poll?.votes || []).map(v => v.participant)).size;
  },

  participantCountLabel(count) {
    return interpolate(ngettext('%(count)s participant', '%(count)s participants', count), { count }, true);
  },

  slotCountLabel(count) {
    return interpolate(ngettext('%(count)s slot', '%(count)s slots', count), { count }, true);
  },

  isChosenSlot(poll, slotUuid) {
    return poll?.status === 'closed' && poll?.chosen_slot_id === slotUuid;
  },

  // The standalone guest page loads no localtime.js, so getUserLocale() may
  // be missing there: <html lang> carries the interface language instead.
  locale() {
    if (window.getUserLocale) return window.getUserLocale();
    return document.documentElement.lang || undefined;
  },

  formatSlotDate(slot) {
    if (!slot?.start) return '';
    const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
    const d = new Date(slot.start);
    return d.toLocaleDateString(this.locale(), { timeZone: tz, weekday: 'short', month: 'short', day: 'numeric' });
  },

  formatSlotTime(slot) {
    if (!slot?.start) return '';
    const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
    const start = new Date(slot.start);
    const parts = [start.toLocaleTimeString(this.locale(), { timeZone: tz, hour: '2-digit', minute: '2-digit' })];
    if (slot.end) {
      const end = new Date(slot.end);
      parts.push(end.toLocaleTimeString(this.locale(), { timeZone: tz, hour: '2-digit', minute: '2-digit' }));
    }
    return parts.join(' - ');
  },
};


