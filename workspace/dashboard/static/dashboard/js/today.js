/**
 * The dashboard's Today card: one list of rows from every source, narrowed
 * in place by a quick filter. The server renders every row; this only
 * decides which ones show.
 *
 * `all` shows the first `limit` rows until the user asks for the rest,
 * `urgent` the urgent rows, any other value the rows of that source.
 */
window.todayCard = function todayCard(limit) {
  return {
    filter: 'all',
    expanded: false,
    limit: limit,
    pick(filter) {
      this.filter = filter;
      this.expanded = false;
    },
    shows(source, urgent, index) {
      if (this.filter === 'all') return this.expanded || index < this.limit;
      if (this.filter === 'urgent') return urgent;
      return source === this.filter;
    },
  };
};

/**
 * Runs the tasks handed to it one after the other, each once the previous
 * one settled. Two quick toggles otherwise race: the older list can land
 * last and overwrite the newer one.
 */
window.todaySaveQueue = function todaySaveQueue() {
  let tail = Promise.resolve();
  return (task) => {
    tail = tail.then(task, task);
    return tail;
  };
};
