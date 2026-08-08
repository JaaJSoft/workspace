// Point d'entrée du bundle Alpine vendorisé.
//
// L'ordre est contraignant. `Alpine.plugin()` doit s'exécuter avant
// `Alpine.start()`, et c'est `Alpine.start()` qui émet `alpine:init` — soit
// l'événement que `workspace/common/static/ui/js/stores.js` attend pour
// enregistrer les stores `presence`, `notifications` et `push`. Ce bundle étant
// chargé avec `defer` depuis <head>, il s'exécute après les scripts non
// différés de fin de <body>, qui ont donc déjà posé leurs écouteurs.
import Alpine from 'alpinejs';
import collapse from '@alpinejs/collapse';
import ajax from '@imacrayon/alpine-ajax';

window.Alpine = Alpine;

Alpine.plugin(collapse);
Alpine.plugin(ajax);

Alpine.start();
