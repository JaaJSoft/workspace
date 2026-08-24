function onboardingWizard(pending) {
  return {
    step: 1,
    completed: !pending,
    next() {
      this.step = Math.min(3, this.step + 1);
      this.focusStep();
    },
    prev() {
      this.step = Math.max(1, this.step - 1);
      this.focusStep();
    },
    focusStep() {
      // Move focus to the active step's heading so screen readers announce
      // the new step (the body is aria-live, but focusing the heading gives
      // a reliable landing point for keyboard users too).
      this.$nextTick(() => {
        const heading = this.$root.querySelector(
          `[data-step="${this.step}"] [data-step-title]`
        );
        if (heading) heading.focus();
      });
    },
    markCompleteIfNeeded() {
      if (this.completed) return Promise.resolve();
      this.completed = true;
      // keepalive lets the request finish even when the click also navigates
      // away (module cards, "Get started"); .catch keeps a network failure
      // from bubbling as an unhandled rejection.
      return fetch('/api/v1/settings/core/onboarding_completed', {
        method: 'PUT',
        credentials: 'same-origin',
        keepalive: true,
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify({
          value: true
        }),
      }).catch(() => {});
    },
    async goTo(url) {
      // Persist completion BEFORE navigating: the destination page runs the
      // same context processor and would re-open the tour if the flag isn't
      // written yet. Awaiting closes that race.
      await this.markCompleteIfNeeded();
      window.location.href = url;
    },
    openChangelog() {
      // Close this dialog first - the native close event will fire and
      // run markCompleteIfNeeded(). Then ask the changelog dialog to open.
      // $root is the dialog (the x-data host); $el here would be the
      // button that triggered the click.
      this.$root.close();
      window.dispatchEvent(new CustomEvent('changelog-open'));
    },
  };
}
