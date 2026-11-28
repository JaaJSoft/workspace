/* Calendar hover cards (events and tasks), on the generic card_popover.js engine. */

window.calendarCard = window.createHoverCard({ className: 'event-card-popover', minSpace: 240 });

/**
 * Show the card for a calendar event. One occurrence of a recurring event
 * is named "seriesUuid:isoDate": the series is what the endpoint knows about,
 * the slot is what tells it which occurrence to show.
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
  window.calendarCard.show(wrapper, url);
};
