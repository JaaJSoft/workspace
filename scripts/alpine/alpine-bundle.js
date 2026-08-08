// Entry point for the vendored Alpine bundle.
//
// Order is load-bearing. `Alpine.plugin()` must run before `Alpine.start()`,
// and it's `Alpine.start()` that fires `alpine:init` - the event
// `workspace/common/static/ui/js/stores.js` waits for to register the
// `presence`, `notifications` and `push` stores. Since this bundle loads with
// `defer` from <head>, it runs after the non-deferred end-of-body scripts,
// which have already attached their listeners by then.
import Alpine from 'alpinejs';
import collapse from '@alpinejs/collapse';
import ajax from '@imacrayon/alpine-ajax';

window.Alpine = Alpine;

Alpine.plugin(collapse);
Alpine.plugin(ajax);

Alpine.start();
