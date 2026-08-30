# Positionnement tarifaire - hébergement managé et expertise

Le produit est libre et auto-hébergeable sans limite d'utilisateurs. On ne vend
donc pas une licence, on vend deux choses :

1. **De l'hébergement managé** pour ceux qui ne veulent pas opérer l'instance ;
2. **De l'expertise** pour les déploiements de plusieurs milliers d'utilisateurs.

La tarification de l'hébergement se fait **à la ressource** (vCPU / RAM /
stockage), le nombre d'utilisateurs n'étant qu'un ordre de grandeur indicatif.

Valeurs HT, converties à 1 USD ≈ 0,92 EUR. Relevé du 30 août 2026.

## 1. Ce que ça change par rapport à un modèle de licence

Vendre une licence, c'est vendre un droit d'usage : la marge est proche de 100 %
et le prix est arbitraire. Vendre de l'hébergement, c'est revendre du matériel
avec du service par-dessus : **le prix a un plancher** (le coût infra) et
**le concurrent n'est plus Nextcloud, ce sont les hébergeurs Nextcloud**.

Ces deux marchés n'ont pas les mêmes prix :

| | Éditeurs de licence | Hébergeurs managés |
|---|---|---|
| Référence | Nextcloud Enterprise 68-195 €/user/an | Hetzner Storage Share 5 €/mois, 1 To, users illimités |
| Ce qui est facturé | Le siège | La ressource ou le forfait |
| Plancher de prix | Aucun | Coût du serveur + du stockage |

## 2. Le marché de l'hébergement managé

| Offre | Prix | Modèle |
|---|---|---|
| **Hetzner Storage Share** (Nextcloud managé) | **≈ 5 €/mois** (NX11, 1 To) · 14,19 € (NX21) | Par palier de stockage, **utilisateurs illimités** |
| IONOS Nextcloud | 6 £ (≤ 5 users) · 9 £ (≤ 10) · 20 £ (≤ 25) · 45 £ (≤ 50) | Forfait par tranche d'utilisateurs |
| o2switch Cloud | 22,32 €/an, Nextcloud 250 Go inclus | Forfait, adossé à l'hébergement web |
| LRob (FR) | dès **360 €/an**, utilisateurs illimités, sauvegardes incluses | Forfait par instance |
| Le Coq Numérique (FR) | Kit déploiement 199 € · Cloud managé **69 €/mois** · dédié sur devis | Forfait par instance |
| ASC2SI (FR) | Infogérance Nextcloud dès **280 €/mois** | Contrat de service |
| Cloudeezy (FR) | Infrastructure dédiée par client, support 24/7 | Sur devis |
| PikaPods | dès **1,80 $/mois**, ajustable au CPU/RAM/stockage | **À la ressource** |
| Elestio | ≈ **17 $/mois par app** | À l'instance, fully managed |
| Ghost(Pro) | 15 $ · 29 $ · 199 $/mois, palier au nombre de membres | Forfait à la métrique d'usage |

**Le point dur : Hetzner Storage Share à 5 €/mois pour 1 To et des utilisateurs
illimités.** On ne bat pas ce prix, et il ne faut pas essayer. On le contourne :
Storage Share, c'est du stockage de fichiers ; Workspace, c'est fichiers + mail
+ agenda + chat + projets + coffre-fort, opéré par celui qui écrit le code.

Le repère haut est tout aussi utile : **ASC2SI facture 280 €/mois** pour de
l'infogérance Nextcloud, et une PME de 30 personnes paie 6 000 à 9 000 €/an chez
un intégrateur français, contre ~13 000 € chez Google Workspace. La fourchette
crédible du managé français va donc de 69 à 280 €/mois pour une PME.

## 3. Coût de revient de l'infrastructure

| Poste | Prix constaté |
|---|---|
| VPS 4 vCPU / 8 Go | OVHcloud ≈ 6,46 $/mois · Hetzner CX32 ≈ 7,88 $ · Scaleway ≈ 14 € |
| Stockage objet S3 | Hetzner ≈ **5,99 €/To/mois** (1 To de trafic inclus, puis 1,20 €/To) |
| Stockage bloc / Storage Box | Hetzner BX11 ≈ **3,20 €/To/mois** |

À noter : **le coût de la RAM a augmenté d'environ 30 % depuis fin 2025** et OVH
comme Scaleway ont répercuté. La RAM est le poste qui se tend, pas le stockage -
raison de plus pour indexer le prix sur le couple vCPU/RAM et facturer le
stockage à part.

## 4. Modèle de dimensionnement

Ressources demandées par le déploiement Kubernetes de référence
(`docs/deployments/kubernetes/app.yaml`), hors PostgreSQL :

| Composant | CPU demandé | RAM demandée | RAM max |
|---|---|---|---|
| App (gunicorn gevent, 3 workers) | 200 m | 256 Mi | 512 Mi |
| Celery worker | 100 m | 1024 Mi | 2048 Mi |
| Celery beat | 50 m | 512 Mi | 1024 Mi |
| Redis | 50 m | 512 Mi | 1024 Mi |
| Collabora (optionnel) | 250 m | 512 Mi | 2048 Mi |

Socle ≈ **0,4 vCPU / 2,3 Go**, hors base de données et hors édition
collaborative.

### Grille de capacité indicative

Ces chiffres sont **une estimation à valider par test de charge**, pas une
mesure. Ils supposent 20 % d'utilisateurs actifs simultanés et un usage mixte
fichiers/chat/agenda.

| Ressources | Utilisateurs indicatifs | Ce qui sature en premier |
|---|---|---|
| 1 vCPU / 2 Go | 1 à 10 | RAM (socle incompressible) |
| 2 vCPU / 4 Go | ~25 | Connexions SSE + Celery |
| 4 vCPU / 8 Go | ~75 | CPU sur les vignettes et la recherche |
| 8 vCPU / 16 Go | ~200 | PostgreSQL, à externaliser à ce stade |
| 16 vCPU / 32 Go | ~500 | Passage en multi-nœuds recommandé |

Trois facteurs font exploser ces ordres de grandeur, et doivent donc être
facturés séparément :

- **L'édition collaborative** (Collabora / OnlyOffice) : chaque document ouvert
  simultanément consomme de l'ordre de 0,3 à 0,5 vCPU et 200 à 300 Mo. C'est le
  premier multiplicateur de coût.
- **La synchronisation IMAP** : le module mail fait tourner Celery en continu,
  proportionnellement au nombre de comptes et au volume de messages.
- **Les connexions SSE** : chat et notifications tiennent une connexion ouverte
  par onglet actif. Peu coûteux en gevent, mais dimensionne le nombre de
  connexions PostgreSQL et la RAM.

**Le produit expose déjà `/metrics` au format Prometheus.** C'est ce qui permet
de transformer un nombre d'utilisateurs flou en engagement mesurable - voir §7.

## 5. Grille d'hébergement proposée

Prix mensuels HT, engagement mensuel ; -15 % à l'année. Le nombre d'utilisateurs
est indicatif et **n'est jamais bloqué** : c'est la ressource qui est vendue.

| Offre | Ressources | Users indicatifs | Stockage inclus | Prix/mois | Coût infra estimé | €/user/mois |
|---|---|---|---|---|---|---|
| **Perso** | 1 vCPU / 2 Go | 1-10 | 100 Go | **12 €** | ≈ 4 € | 1,20 à 12 € |
| **Équipe** | 2 vCPU / 4 Go | ~25 | 500 Go | **39 €** | ≈ 9 € | 1,56 € |
| **Studio** | 4 vCPU / 8 Go | ~75 | 1 To | **89 €** | ≈ 16 € | 1,19 € |
| **Entreprise** | 8 vCPU / 16 Go | ~200 | 2 To | **189 €** | ≈ 32 € | 0,95 € |
| **Dédié** | 16 vCPU / 32 Go, instance isolée | ~500 | 4 To | **379 €** | ≈ 68 € | 0,76 € |
| **Sur mesure** | Cluster, HA, PostgreSQL managé | 1 000+ | sur devis | **dès 900 €** | - | - |

Options, facturées à part parce que ce sont les vrais postes de coût :

| Option | Prix |
|---|---|
| Stockage supplémentaire | **25 €/To/mois** (coût ≈ 3 à 6 €) |
| Édition collaborative (Collabora) | **+29 €/mois**, jusqu'à 10 documents simultanés |
| Sauvegarde externalisée quotidienne, rétention 30 j | **+15 %** du plan |
| Instance dédiée (pas de mutualisation) | **+50 %** du plan |
| Migration des données à l'entrée | dès **490 €** |

### Pourquoi ces montants tiennent

- **La marge brute est de 70 à 82 % et croît avec la taille du plan**, parce que
  le coût par vCPU baisse quand le serveur grossit. La progression est saine :
  les gros comptes financent le support des petits.
- **Le prix par utilisateur décroît de 1,56 € à 0,76 €/mois**, ce qui place
  l'offre sous Infomaniak kSuite Standard (1,90 €) et à un dixième de Google
  Workspace ou Microsoft 365 (14 $).
- **L'argument de vente est le prix affiché** : « Studio à 89 €/mois pour 75
  personnes » se compare à « six sièges Google Workspace ».
- **Le palier Perso à 12 €** ne cherche pas à battre Hetzner à 5 € : il vend une
  suite complète là où Hetzner vend du stockage, et il sert d'entrée de gamme
  vers les paliers rentables.

## 6. Grille d'expertise

C'est là qu'est la marge sur les gros déploiements, et le marché la supporte :
le TJM DevOps médian en France est de **680 €**, un senior se situe entre 800 et
950 €, un architecte entre 1 000 et 1 300 €. Un éditeur qui intervient sur son
propre code n'est pas un prestataire DevOps générique - il se place **en haut de
la fourchette**.

| Prestation | Prix | Note |
|---|---|---|
| Journée d'expertise | **950 €/jour** | Base de facturation de tout le reste |
| Audit d'architecture et cadrage | **2 900 €** (3 j) | Livrable : dimensionnement et plan de déploiement |
| Déploiement clé en main on-premise ou K8s | **6 500 à 12 000 €** | 5 à 10 jours selon l'intégration SSO/LDAP |
| Migration depuis Nextcloud, Google ou M365 | dès **4 500 €** | Données, comptes, partages |
| Développement spécifique / module métier | au TJM | Reste dans le tronc commun si générique |

Contrats de support annuels - c'est le récurrent qui compte :

| Niveau | Engagement | Prix/an |
|---|---|---|
| **Bronze** | Réponse 3 j ouvrés, mises à jour testées et annoncées | **3 900 €** |
| **Silver** | Réponse 1 j ouvré, correctifs prioritaires, 2 interventions incluses | **9 900 €** |
| **Gold** | SLA 4 h ouvrées, astreinte, influence sur la feuille de route | **24 900 €** |

**Le calcul qui emporte un compte de 2 000 personnes :** Gold à 24 900 €/an, soit
**12,45 €/user/an**, contre un minimum de 135 000 €/an chez Nextcloud Standard à
la même échelle - licence à laquelle il faut encore ajouter l'hébergement. Le
repère bas reste ASC2SI à 280 €/mois (3 360 €/an), qui borne l'entrée de gamme
du support français : Bronze doit rester dans cet ordre de grandeur.

## 7. Le point opérationnel à ne pas rater

Vendre de la ressource sans plafonner les utilisateurs crée un risque unique :
**le client met 400 personnes sur un plan à 2 vCPU, puis reproche la lenteur.**
Sans garde-fou, la conversation devient un procès en qualité de service.

La réponse est déjà dans le produit : `/metrics` expose Prometheus. Il faut donc
vendre, non pas un nombre d'utilisateurs, mais **un engagement de performance
mesuré** :

- Chaque palier porte un engagement du type « p95 sous 500 ms sur les vues
  principales ».
- La supervision détecte le dépassement et **déclenche une proposition de
  montée de gamme argumentée par les métriques du client lui-même**.
- L'upsell devient factuel et non commercial : ce n'est pas le vendeur qui
  décide que le client est trop gros, c'est son instance.

C'est un différenciateur réel face aux forfaits par tranche d'utilisateurs
(IONOS) comme face aux forfaits de stockage (Hetzner) : les deux facturent une
métrique qui n'a aucun rapport avec la performance ressentie.

## 8. Points de vigilance

- **Ne jamais brider la Community Edition.** C'est la prémisse du modèle, et
  c'est aussi ce que font Nextcloud CE, Seafile CE et Carbonio CE. Le jour où
  l'édition libre devient inutilisable, l'audience qui alimente le canal
  disparaît.
- **Ne pas indexer le prix sur le seul stockage.** Il est bon marché (3 à 6 €/To)
  et c'est le CPU/RAM qui coûte. Un client « peu de fichiers, beaucoup de mail »
  paierait trop peu ; un client « beaucoup d'archives froides » paierait trop.
- **L'égress et la sauvegarde ne sont pas dans le prix du stockage.** Hetzner
  inclut 1 To de trafic puis facture 1,20 €/To ; une restauration complète de
  4 To pour un client mécontent se chiffre.
- **La RAM se tend (+30 % depuis fin 2025).** Prévoir une clause d'indexation
  annuelle sur les contrats pluriannuels, sinon la marge est mangée en silence.
- **Le support est le vrai coût.** À 39 €/mois, deux tickets par an consomment la
  marge annuelle du plan. Les délais affichés doivent être tenables en solo :
  3 jours ouvrés sur les petits plans n'est pas une faiblesse commerciale, c'est
  la condition de survie du modèle.
- **Séparer l'hébergement de l'expertise dans le discours.** Un client qui
  achète du 89 €/mois et un client qui achète 12 000 € de déploiement n'ont ni
  le même cycle de vente ni le même interlocuteur. Deux pages, deux tarifs.

## Sources

- Hetzner Storage Share : https://io.bikegremlin.com/40172/hetzner-storage-share-managed-nextcloud/ · https://www.whtop.com/plans/hetzner.com/128274
- Hetzner stockage : https://www.hetzner.com/storage/object-storage/ · https://hostbrr.com/storagebox-vs-hetzner-vs-s3.html
- IONOS Nextcloud : https://www.ionos.co.uk/digitalguide/server/tools/nextcloud-hosts/
- Hébergeurs Nextcloud français : https://www.lecoqnumerique.fr/services/cloud-souverain-collaboratif/tarifs · https://www.lrob.fr/en/web-hosting/nextcloud-private-cloud/ · https://www.asc2si.fr/prestations/independance-open-source · https://cloudeezy.com/hebergement-nextcloud.html
- PikaPods / Elestio : https://www.pikapods.com/ · https://dev.to/vikasprogrammer/i-compared-6-platforms-for-deploying-self-hosted-apps-in-2026-3j8
- Ghost(Pro) : https://thatmarketingbuddy.com/pricing/ghost
- Coûts VPS : https://getdeploying.com/hetzner-vs-ovh · https://abdulkadersafi.com/blog/vps-prices-are-rising-everywhere-in-2026-hetzner-ovhcloud-hostinger
- TJM France : https://www.lafabriquedunet.fr/agences/pages/agences-devops/tarifs · https://travail-industrie.com/simulateur-tjm/developpement-it/devops-sre/expert · https://liora.io/tjm-devops
- Nextcloud Enterprise (repère haut) : https://www.trustradius.com/products/nextcloud/pricing
- Infomaniak kSuite : https://www.infomaniak.com/en/support/faq/80/discover-ksuite-and-its-products
- Microsoft 365 / Google Workspace : https://redriver.com/collaboration/microsoft-365-price-increase-2026 · https://workspace.google.com/pricing
