/**
 * Runs the tasks handed to it one after the other, each once the previous
 * one settled. Two quick toggles otherwise race: the older list can land
 * last and overwrite the newer one.
 */
window.dashboardSaveQueue = function dashboardSaveQueue() {
  let tail = Promise.resolve();
  return (task) => {
    tail = tail.then(task, task);
    return tail;
  };
};

/**
 * The dashboard settings popover: temperature unit, module tiles and Today
 * sources. Every save goes through one queue, so the last toggle is the one
 * the server keeps.
 */
window.dashboardPrefsForm = function dashboardPrefsForm(options) {
  const readJson = (id) => JSON.parse(document.getElementById(id).textContent);
  const apps = readJson('dashboard-apps-data');
  return {
    prefsOpen: false,
    temperatureUnit: options.temperatureUnit,
    apps: apps,
    hidden: apps.filter(a => a.hidden).map(a => a.slug),
    todaySources: readJson('dashboard-today-sources-data'),
    hiddenToday: readJson('dashboard-hidden-today-data'),
    saves: window.dashboardSaveQueue(),
    isVisible(slug) {
      return !this.hidden.includes(slug);
    },
    isTodayShown(slug) {
      return !this.hiddenToday.includes(slug);
    },
    toggleToday(slug, shown) {
      this.hiddenToday = shown ?
        this.hiddenToday.filter(s => s !== slug) : [...this.hiddenToday, slug];
      // Persist first, then re-fetch the card: the server decides whether
      // the card or the empty hidden slot comes back.
      this.save('hidden_today_sources', () => this.hiddenToday).then(() => {
        this.$ajax(options.todayUrl, { target: 'dashboard-today' });
      });
    },
    setTemperatureUnit() {
      this.save('temperature_unit', () => this.temperatureUnit);
      this.$dispatch('temperature-unit-changed', this.temperatureUnit);
    },
    toggleApp(slug, visible) {
      this.hidden = visible ?
        this.hidden.filter(s => s !== slug) :
        this.hidden.includes(slug) ? this.hidden : [...this.hidden, slug];
      // Persist first, then re-render the grid from the server so the tile
      // appears/disappears live without a full page reload.
      this.save('hidden_modules', () => this.hidden).then(() => {
        this.$ajax(options.modulesUrl, { target: 'dashboard-modules-grid' });
      });
    },
    // Queued, and reading the value when it runs: the last save sends the latest state.
    save(key, readValue) {
      return this.saves(() => fetch('/api/v1/settings/dashboard/' + key, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRFToken()
        },
        body: JSON.stringify({ value: readValue() }),
      }));
    },
  };
};
