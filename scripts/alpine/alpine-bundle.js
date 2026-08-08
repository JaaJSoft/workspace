// Entry point for the vendored Alpine bundle.
//
// Order is load-bearing: every `Alpine.plugin()` must run before
// `Alpine.start()`, which fires the `alpine:init` event that stores.js
// listens for.
import Alpine from 'alpinejs';
import collapse from '@alpinejs/collapse';
import ajax from '@imacrayon/alpine-ajax';

window.Alpine = Alpine;

Alpine.plugin(collapse);
Alpine.plugin(ajax);

Alpine.start();
