// Last-request-wins for a component that may fire the same kind of request
// again before the previous one answered (switching items, overlapping
// refreshes). Each start() supersedes every earlier request of the tracker;
// after each await the caller asks its request whether it is still current
// and drops the response when it is not.
//
//   const requests = latestOnly();
//   const request = requests.start();
//   const resp = await fetch(url);
//   if (!request.isCurrent()) return;
//
// join() takes part in the current request without superseding it (the next
// page of a list that is still the current one); cancel() supersedes every
// request in flight without starting a new one (closing the panel they fill).
window.latestOnly = function latestOnly() {
  let current = 0;
  const requestFor = (generation) => ({
    isCurrent: () => generation === current,
  });
  return {
    start() {
      current += 1;
      return requestFor(current);
    },
    join() {
      return requestFor(current);
    },
    cancel() {
      current += 1;
    },
  };
};
