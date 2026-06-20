// Chat audio calls: WebRTC mesh over SSE signaling.
//
// Pure helpers (testable via the node:vm loader) are declared as top-level
// functions and mirrored onto window. The Alpine mixin (chatCallMixin) holds
// the RTCPeerConnection wiring and is validated in a real browser.

function chatCallShouldOffer(selfId, newcomerId) {
  // Deterministic glare avoidance: an existing participant offers to a
  // newcomer. "Should I offer to this newcomer?" is true for everyone but
  // the newcomer themselves.
  return selfId !== newcomerId;
}

function chatCallMergeMediaState(current, patch) {
  return Object.assign({}, current || {}, patch || {});
}

function chatCallOtherParticipantIds(participants, selfId) {
  return (participants || [])
    .map((p) => p.user_id)
    .filter((id) => id !== selfId);
}

window.chatCallShouldOffer = chatCallShouldOffer;
window.chatCallMergeMediaState = chatCallMergeMediaState;
window.chatCallOtherParticipantIds = chatCallOtherParticipantIds;
