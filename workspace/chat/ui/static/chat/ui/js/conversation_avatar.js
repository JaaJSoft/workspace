/**
 * <conversation-avatar> — the circle standing for a chat conversation: the
 * other participant's avatar for a direct message, the uploaded group picture
 * for a group that has one, its members' initials otherwise.
 *
 * One implementation for both rendering paths, like <user-avatar>. The sidebar
 * writes the element with server values; the conversation header and the info
 * panel bind the same attributes from Alpine:
 *
 *   <conversation-avatar kind="group" uuid="{{ conv.uuid }}"
 *                        initials="{{ conv.avatar_initial }}" size="sm"></conversation-avatar>
 *   <conversation-avatar :kind="conv.kind" :uuid="conv.uuid"
 *                        :initials="conv.avatar_initial" size="md" presence card></conversation-avatar>
 *
 * Attributes:
 *   - kind        "dm" or "group". Picks the fallback colour, and whether the
 *                 element delegates to <user-avatar>.
 *   - uuid        conversation id, source of the uploaded image.
 *   - user-id     the OTHER participant of a direct message. With it, the
 *                 element renders a <user-avatar> and nothing else here
 *                 applies (presence, hover card, initials colour are that
 *                 element's business).
 *   - username    that participant's name.
 *   - name        conversation name, used as the image's alt text.
 *   - initials    the fallback letters, computed server-side
 *                 (chat.services.avatar.conversation_avatar_initial) so the
 *                 sidebar and the header cannot disagree on them.
 *   - has-avatar  the conversation has an uploaded picture.
 *   - bust        cache-busting token, set after an upload replaces the image.
 *   - size        a key of USER_AVATAR_SIZES (default "md").
 *   - active      the conversation is the selected one: the fallback flips
 *                 from tinted to solid so it reads against the row highlight.
 *   - presence / card  forwarded to <user-avatar> for the direct-message case.
 *
 * Geometry invariant, inherited from <user-avatar>: the initials are ALWAYS
 * rendered and the uploaded image sits on top of them, absolutely positioned
 * in the same box. A missing or broken image uncovers what is already there
 * instead of resizing the element, so a sidebar mixing pictures and initials
 * stays aligned.
 *
 * index.html / room.html load this in <head> without `defer`, so the element
 * upgrades while the document parses and the server-rendered sidebar avatars
 * never flash unstyled.
 */

/**
 * Fallback circle colours. A group's fallback is tinted so it doesn't compete
 * with the row it sits in, and solid when that row is the selected one.
 *
 * @param {string} kind - "dm" or "group"
 * @param {boolean} active
 * @returns {string[]}
 */
window.conversationAvatarFaceClasses = function conversationAvatarFaceClasses(kind, active) {
  if (kind === 'dm') return ['bg-neutral', 'text-neutral-content'];
  return active ? ['bg-info', 'text-info-content'] : ['bg-info/20', 'text-info'];
};

/**
 * The letters drawn when there is no picture. The server sends them; this is
 * only the guard for the conversation shapes it cannot label (a direct
 * message with nobody else in it, a group whose members all left).
 *
 * @param {string} initials
 * @param {string} kind
 * @returns {string}
 */
window.conversationAvatarInitials = function conversationAvatarInitials(initials, kind) {
  const trimmed = (initials || '').trim();
  if (trimmed !== '') return trimmed;
  return kind === 'dm' ? '?' : 'G';
};

(function defineConversationAvatar() {
  class ConversationAvatar extends HTMLElement {
    static get observedAttributes() {
      return [
        'kind', 'uuid', 'user-id', 'username', 'name',
        'initials', 'has-avatar', 'bust', 'size', 'active', 'presence', 'card',
      ];
    }

    connectedCallback() {
      if (!this._connected) {
        this._appliedClasses = [];
        this._connected = true;
      }
      this.render();
    }

    attributeChangedCallback() {
      if (this._connected) this.render();
    }

    get kind() {
      return this.getAttribute('kind') === 'dm' ? 'dm' : 'group';
    }

    render() {
      const size = this.getAttribute('size') || 'md';
      const step = window.USER_AVATAR_SIZES[size] || window.USER_AVATAR_SIZES.md;

      this.classList.remove(...this._appliedClasses);
      this._appliedClasses = window.userAvatarHostClasses(size);
      this.classList.add(...this._appliedClasses);

      const userId = (this.getAttribute('user-id') || '').trim();
      if (this.kind === 'dm' && userId !== '') {
        this.replaceChildren(this._buildUserAvatar(userId, size));
        return;
      }

      const face = document.createElement('span');
      face.className = [
        'relative',
        'w-full',
        'h-full',
        'rounded-full',
        'overflow-hidden',
        'flex',
        'items-center',
        'justify-center',
        'font-medium',
        step.text,
      ].join(' ');
      face.classList.add(
        ...window.conversationAvatarFaceClasses(this.kind, this.hasAttribute('active')),
      );

      const label = document.createElement('span');
      label.className = 'leading-none select-none';
      label.textContent = window.conversationAvatarInitials(
        this.getAttribute('initials'),
        this.kind,
      );
      face.appendChild(label);

      const uuid = (this.getAttribute('uuid') || '').trim();
      if (this.hasAttribute('has-avatar') && uuid !== '') {
        face.appendChild(this._buildImage(uuid));
      }

      this.replaceChildren(face);
    }

    _buildUserAvatar(userId, size) {
      const avatar = document.createElement('user-avatar');
      avatar.setAttribute('user-id', userId);
      avatar.setAttribute('username', this.getAttribute('username') || '');
      avatar.setAttribute('size', size);
      if (this.hasAttribute('presence')) avatar.setAttribute('presence', '');
      if (this.hasAttribute('card')) avatar.setAttribute('card', '');
      return avatar;
    }

    _buildImage(uuid) {
      const img = document.createElement('img');
      const bust = (this.getAttribute('bust') || '').trim();
      const query = bust === '' ? '' : `?t=${encodeURIComponent(bust)}`;
      img.src = `/api/v1/chat/conversations/${encodeURIComponent(uuid)}/avatar/image${query}`;
      img.alt = this.getAttribute('name') || '';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.className = 'absolute inset-0 w-full h-full object-cover';
      // Removing the image uncovers the initials that are already in place.
      img.addEventListener('error', () => img.remove());
      return img;
    }
  }

  if (!customElements.get('conversation-avatar')) {
    customElements.define('conversation-avatar', ConversationAvatar);
  }
})();
