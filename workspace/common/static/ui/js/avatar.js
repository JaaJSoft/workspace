/**
 * Generate avatar HTML for a user.
 * Attempts to load the avatar image; falls back to initials on error.
 *
 * @param {number|string} userId
 * @param {string} username
 * @param {string} sizeClass - Tailwind size classes, e.g. 'w-7 h-7 text-xs'
 * @returns {string} HTML string
 */
window.userAvatarHtml = function(userId, username, sizeClass) {
  const initial = (username || '?')[0].toUpperCase();
  const imgUrl = `/api/v1/users/${userId}/avatar`;

  return `<div class="avatar">` +
    `<div class="${sizeClass} rounded-full overflow-hidden">` +
      `<img src="${imgUrl}" alt="${username}" class="w-full h-full object-cover" ` +
        `onerror="this.onerror=null;` +
        `var d=this.closest('.avatar');` +
        `d.className='avatar placeholder';` +
        `d.firstElementChild.className='${sizeClass} bg-neutral text-neutral-content rounded-full flex items-center justify-center';` +
        `this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${initial}'}));" />` +
    `</div>` +
  `</div>`;
};
