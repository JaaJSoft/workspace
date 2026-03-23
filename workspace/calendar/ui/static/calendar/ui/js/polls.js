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

  isChosenSlot(poll, slotUuid) {
    return poll?.status === 'closed' && poll?.chosen_slot_id === slotUuid;
  },

  formatSlotDate(slot) {
    if (!slot?.start) return '';
    const d = new Date(slot.start);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  },

  formatSlotTime(slot) {
    if (!slot?.start) return '';
    const start = new Date(slot.start);
    const parts = [start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })];
    if (slot.end) {
      const end = new Date(slot.end);
      parts.push(end.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }));
    }
    return parts.join(' - ');
  },
};


