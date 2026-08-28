/* Calendar event hover card, riding on the generic card_popover.js engine. */

/**
 * Show the card for a calendar event. Recurring virtual occurrences carry an
 * id of "masterUuid:isoDate" - the master is what the endpoint knows about,
 * the occurrence start is what tells it which instance to label.
 */
window._eventCardShow = function(wrapper, eventId) {
  let fetchId = eventId;
  let occStart = '';
  const colonIdx = String(eventId).indexOf(':');
  if (colonIdx > 0) {
    fetchId = eventId.substring(0, colonIdx);
    occStart = eventId.substring(colonIdx + 1);
  }
  let url = '/calendar/events/' + fetchId + '/card';
  if (occStart) url += '?start=' + encodeURIComponent(occStart);
  window._cardPopoverShow(wrapper, url);
};
