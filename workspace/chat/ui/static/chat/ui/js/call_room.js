// Voice room: pure helpers shared by the room page and the main-tab observer.
// The Alpine room factory lives in room.js; speaking-meter wiring that touches
// AudioContext is validated in a real browser, not here.

function chatCallRoomUrl(conversationId) {
  return `/chat/room/${conversationId}`;
}

function chatCallRoomTabName(conversationId) {
  // Deterministic tab name: window.open with this name reactivates the
  // existing room tab instead of opening a duplicate.
  return `chat-room-${conversationId}`;
}

function chatCallBannerAction(callActive, participants, currentUserId) {
  // What the main (observer) tab should offer for an ongoing call:
  //   null     -> no active call, hide the banner
  //   'return' -> I am a participant, reactivate my room tab
  //   'join'   -> a call is running but I am not in it
  if (!callActive) return null;
  const inIt = (participants || []).some((p) => p.user_id === currentUserId);
  return inIt ? 'return' : 'join';
}

function chatIsSpeaking(level, threshold) {
  const t = (typeof threshold === 'number') ? threshold : 0.05;
  return typeof level === 'number' && level >= t;
}

function chatCallShouldOwnMedia(role) {
  // Only the observer role gives up the microphone / peer connections.
  return role !== 'observer';
}

function chatCallAutoPinTarget(participants, pinnedManually) {
  // The automatic spotlight pick: the first participant actively sharing their
  // screen. A manual pin always wins, so we yield when one is set. Derived from
  // the live participants list, so it reflects who is sharing *now*.
  if (pinnedManually) return null;
  const sharer = (participants || []).find(
    (p) => p && p.media_state && p.media_state.screen === true,
  );
  return sharer ? sharer.user_id : null;
}

function chatCallSpotlightTarget(participants, pinnedUserId, pinnedManually) {
  // Which participant to show large. A manual pin wins while that participant is
  // still in the call; otherwise the spotlight is derived from live state - the
  // active screen sharer, or the equal grid (null). Deriving instead of latching
  // a one-off event means a sharer is spotlighted even for someone who joined
  // after the share began, and the spotlight clears the moment sharing stops.
  const list = participants || [];
  if (pinnedManually && pinnedUserId != null) {
    return list.some((p) => p.user_id === pinnedUserId) ? pinnedUserId : null;
  }
  return chatCallAutoPinTarget(list, pinnedManually);
}

window.chatCallRoomUrl = chatCallRoomUrl;
window.chatCallRoomTabName = chatCallRoomTabName;
window.chatCallBannerAction = chatCallBannerAction;
window.chatIsSpeaking = chatIsSpeaking;
window.chatCallShouldOwnMedia = chatCallShouldOwnMedia;
window.chatCallSpotlightTarget = chatCallSpotlightTarget;
window.chatCallAutoPinTarget = chatCallAutoPinTarget;
