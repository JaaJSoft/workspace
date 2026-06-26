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

function chatCallSpotlightTarget(participants, pinnedUserId) {
  // Which participant to show large. Pure derivation: the viewer's pin if that
  // participant is still in the call, otherwise the equal grid (null).
  if (pinnedUserId == null) return null;
  const present = (participants || []).some((p) => p.user_id === pinnedUserId);
  return present ? pinnedUserId : null;
}

function chatCallAutoPinTarget(participant, pinnedManually) {
  // Intelligent default: when a participant turns their screen share on and the
  // viewer has not made an explicit pin choice, auto-pin that sharer. A manual
  // pin always wins, so we yield.
  if (pinnedManually) return null;
  if (participant && participant.media_state && participant.media_state.screen === true) {
    return participant.user_id;
  }
  return null;
}

window.chatCallRoomUrl = chatCallRoomUrl;
window.chatCallRoomTabName = chatCallRoomTabName;
window.chatCallBannerAction = chatCallBannerAction;
window.chatIsSpeaking = chatIsSpeaking;
window.chatCallShouldOwnMedia = chatCallShouldOwnMedia;
window.chatCallSpotlightTarget = chatCallSpotlightTarget;
window.chatCallAutoPinTarget = chatCallAutoPinTarget;
