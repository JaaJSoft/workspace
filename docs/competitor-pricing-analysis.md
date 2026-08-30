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

Gamme VPS OVHcloud 2027, tarifs « à partir de » HT relevés sur la page
publique. Trafic illimité et sauvegarde automatisée à un jour incluses partout.

| Offre | vCores | RAM | Disque NVMe | Bande passante | Prix HT/mois | € par Go de RAM |
|---|---|---|---|---|---|---|
| VPS-1 | 2 | 4 Go | 40 Go | 500 Mbit/s | **3,81 €** | 0,95 € |
| VPS-2 | 4 | 8 Go | 75 Go | 1 Gbit/s | **7,21 €** | 0,90 € |
| VPS-3 | 6 | 12 Go | 100 Go | 2 Gbit/s | **10,40 €** | 0,87 € |
| VPS-4 | 8 | 24 Go | 200 Go | 3 Gbit/s | **19,96 €** | 0,83 € |

Disques additionnels pour VPS, et comparaison avec l'objet :

| Poste | Prix HT/mois | Ramené au To |
|---|---|---|
| Disque additionnel 50 Go | 5,50 € | **110 €/To** |
| Disque additionnel 100 Go | 11,00 € | **110 €/To** |
| Disque additionnel 200 Go | 16,50 € | **82,50 €/To** |
| Disque additionnel 500 Go | 33,00 € | **66 €/To** |
| Stockage objet S3, classe Standard | ≈ 7 €/To | **7 €/To**, sortie gratuite |
| Kubernetes managé | control plane **gratuit** | dès ~18 €/mois |

**Le bloc coûte 9 à 16 fois l'objet.** C'est le chiffre qui commande toute la
grille - voir §5.

**Ne jamais acheter un disque additionnel : monter d'un cran de VPS.** Pour
chaque taille d'add-on, le VPS supérieur coûte autant ou moins et apporte le CPU
et la RAM en plus.

| Besoin | Via disque additionnel | Via VPS supérieur |
|---|---|---|
| ~90 Go | VPS-1 + 50 Go = 9,31 € (2c / 4 Go) | VPS-3 = 10,40 € (6c / 12 Go, 100 Go) |
| ~240 Go | VPS-1 + 200 Go = 20,31 € (2c / 4 Go) | **VPS-4 = 19,96 €** (8c / 24 Go, 200 Go) |

Au-delà de 200 Go, aucun des deux ne tient : c'est l'objet, ou rien.

Trois choses à lire dans ce tableau :

- **Le prix du Go de RAM est plat** — 0,95 € sur le VPS-1, 0,83 € sur le VPS-4.
  Il n'y a pas de remise de volume : consolider plusieurs tenants sur une grosse
  machine ne fait pas gagner sur le prix unitaire, seulement sur le remplissage
  de la marge inutilisée.
- **Le disque du VPS est la ressource chère.** 40 Go pour 3,81 € revient à
  environ 95 €/To/mois si on raisonne au stockage, contre 7 €/To en objet.
  Facteur 13. C'est ce qui rend la sortie des blobs vers S3 économiquement
  décisive et pas seulement architecturalement propre.
- **Le trafic illimité supprime une ligne de coût variable.** Pour une suite dont
  l'essentiel du trafic est de l'upload et du download de fichiers, c'est un
  avantage plus important que les quelques euros d'écart sur la machine.

À noter également : **le coût de la RAM a augmenté d'environ 30 % depuis fin
2025**, et OVH a répercuté par une hausse allant jusqu'à 45 % au 1er avril 2026.
La volatilité est démontrée, pas hypothétique — d'où la clause d'indexation
recommandée au §8.

Enfin, ces prix sont des tarifs « à partir de », donc très probablement à
engagement annuel. La facturation mensuelle est plus chère chez OVH : **ne vous
engagez à l'année sur un VPS que pour un client engagé à l'année chez vous**,
sinon vous portez seul le risque de résiliation.

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

### SQLite ou PostgreSQL : la frontière tombe sur les paliers

Sans `REDIS_URL`, `workspace/settings/celery.py` bascule le broker sur
`memory://` avec `CELERY_TASK_ALWAYS_EAGER`, et `cache.py` retombe en mémoire.
Un petit tenant est donc **un seul conteneur** - ni Redis, ni worker, ni beat, ni
PostgreSQL - qui consomme de l'ordre de 300 à 400 Mo, et non les 2,3 Go de socle
du manifeste Kubernetes, lequel décrit la stack complète.

SQLite est configuré sérieusement dans `workspace/settings/db.py` : WAL,
`synchronous=NORMAL`, `busy_timeout=60000`, `transaction_mode=IMMEDIATE`. Sur du
NVMe local c'est excellent. Deux limites décident du palier :

- **SQLite n'a qu'un écrivain à la fois.** Avec `IMMEDIATE` et un busy_timeout de
  60 s, la contention se manifeste en latence, pas en erreurs - le bon réglage,
  mais la dégradation est silencieuse. Chat, SSE, accusés de lecture et présence
  génèrent beaucoup de petites écritures : confortable jusqu'à ~25 utilisateurs,
  risqué au-delà.
- **SQLite sur stockage réseau est à proscrire.** Sur du bloc répliqué (Longhorn,
  Ceph RBD) chaque fsync part sur le réseau et le débit transactionnel s'effondre.
  Sur du fichier partagé (NFS, CephFS) le verrouillage est cassé et le risque est
  la corruption, pas la lenteur. C'est l'argument décisif en faveur du VPS à
  disque local pour le bas de gamme.

`workspace/core/management/commands/migrate_to_postgres.py` existe déjà : le
passage n'est donc pas une urgence à gérer mais une étape de montée de gamme
productisée.

| Palier | Base | Placement |
|---|---|---|
| Perso, Équipe | SQLite WAL, un conteneur | VPS dédié, NVMe local |
| Studio, Entreprise | PostgreSQL + Redis + Celery | VPS plus gros ou nœud dédié |
| Sur mesure | PostgreSQL managé | Kubernetes managé |

**Le produit expose déjà `/metrics` au format Prometheus.** C'est ce qui permet
de transformer un nombre d'utilisateurs flou en engagement mesurable - voir §7.

## 5. Grille d'hébergement proposée

Prix mensuels HT, engagement mensuel ; -15 % à l'année. Le nombre d'utilisateurs
est indicatif et **n'est jamais bloqué** : c'est la ressource qui est vendue.

Un tenant = un VPS OVHcloud dédié, dans la géolocalisation la plus proche du
client. **Les blobs vont dans le stockage objet, jamais sur un disque de VPS.**
Ce n'est pas une préférence d'architecture, c'est ce qui sépare une entreprise
rentable d'une entreprise déficitaire :

| Palier | Prix | Stockage promis | Servi en disque VPS | Marge | Servi en objet | Marge |
|---|---|---|---|---|---|---|
| Perso | 12 € | 100 Go | 14,81 € | **−23 %** | 4,51 € | **62 %** |
| Équipe | 39 € | 500 Go | 40,21 € | **−3 %** | 10,71 € | **73 %** |
| Studio | 89 € | 1 To | 76,40 € | 14 % | 17,40 € | **80 %** |
| Entreprise | 189 € | 2 To | 151,96 € | 20 % | 33,96 € | **82 %** |
| Dédié | 379 € | 4 To | ≈ 314 € | 17 % | ≈ 78 € | **79 %** |

Servis depuis des disques additionnels, les deux paliers d'entrée sont vendus à
perte et les autres tombent sous 20 % de marge. **Tant que les blobs ne sont pas
sortis vers l'objet, cette grille tarifaire ne peut pas être publiée** - voir §7
pour le chantier correspondant.

| Offre | VPS | Users indicatifs | Stockage inclus | Prix/mois | Coût VPS | Coût objet | Marge |
|---|---|---|---|---|---|---|---|
| **Perso** | VPS-1 · 2c / 4 Go | 1-10 | 100 Go | **12 €** | 3,81 € | 0,70 € | **62 %** |
| **Équipe** | VPS-2 · 4c / 8 Go | ~25 | 500 Go | **39 €** | 7,21 € | 3,50 € | **73 %** |
| **Studio** | VPS-3 · 6c / 12 Go | ~75 | 1 To | **89 €** | 10,40 € | 7,00 € | **80 %** |
| **Entreprise** | VPS-4 · 8c / 24 Go | ~200 | 2 To | **189 €** | 19,96 € | 14,00 € | **82 %** |
| **Dédié** | 2× VPS-4 ou Advance | ~500 | 4 To | **379 €** | ≈ 40 à 60 € | 28,00 € | **77 à 82 %** |
| **Sur mesure** | Kubernetes managé, PostgreSQL managé | 1 000+ | sur devis | **dès 900 €** | - | - | - |

Le prix par utilisateur décroît de 1,56 € à 0,76 €/mois, ce qui place l'offre
sous Infomaniak kSuite Standard (1,90 €) et à un dixième de Google Workspace ou
Microsoft 365 (14 $).

Options, facturées à part parce que ce sont les vrais postes de coût :

| Option | Prix | Coût |
|---|---|---|
| Stockage supplémentaire | **25 €/To/mois** | ≈ 7 €/To en objet |
| Édition collaborative (Collabora) | **+29 €/mois**, 10 documents simultanés | un palier de VPS |
| Sauvegarde 30 jours vers l'objet | **+15 %** du plan | ≈ 0,10 à 0,70 €/tenant |
| Instance dédiée sans mutualisation | **+50 %** du plan | perte de densité |
| Migration des données à l'entrée | dès **490 €** | prestation ponctuelle |

### Pourquoi ces montants tiennent

- **La marge va de 62 % à 82 % et croît avec la taille du plan.** Elle ne vient
  pas d'une remise de volume - le prix du Go de RAM est plat chez OVH - mais du
  fait que le socle applicatif est fixe : un tenant de 200 personnes ne coûte pas
  vingt fois un tenant de 10.
- **Le palier Perso est un produit d'appel, pas un produit rentable.** À 62 % de
  marge, il rapporte environ 90 € brut par an. Une seule heure d'intervention
  manuelle dans l'année, au TJM du §6, coûte davantage. Il n'est vendable que si
  le cycle de vie d'un tenant est intégralement automatisé - voir §7.
- **L'argument de vente est le prix affiché** : « Studio à 89 €/mois pour 75
  personnes » se compare à « six sièges Google Workspace ».
- **La sauvegarde OVH incluse ne suffit pas.** Un jour de rétention couvre le
  fichier supprimé hier, pas une corruption découverte trois jours plus tard ni
  la suppression d'un tenant repérée en fin de mois. La sauvegarde applicative
  vers l'objet reste obligatoire, et c'est elle qu'on facture en option.

### Densité : ce qu'on laisse sur la table en restant en 1:1

Un tenant de 5 personnes consomme environ 400 Mo sur les 4 Go du VPS-1 - 10 % de
la machine. Consolider huit tenants de ce profil sur un VPS-2 ramène le coût à
0,90 € par tenant au lieu de 3,81 €, et la marge du palier Perso de 62 % à 92 %.

**Rester en 1:1 quand même, au démarrage.** C'est plus simple à automatiser, ça
se vend (« votre serveur, dans votre pays »), et l'écart représente environ 36 €
par client et par an - réel, mais pas existentiel. La densité est une
optimisation qui n'a de sens qu'avec le volume qui la justifie.

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

### Le cycle de vie d'un tenant doit coûter zéro minute

À 62 % de marge, le palier Perso rapporte environ 90 € brut par an. Au TJM du
§6, **une heure d'intervention manuelle sur un client dans l'année coûte plus
que ce que ce client rapporte.** Ce n'est pas la marge qui contraint le bas de
gamme, c'est le temps humain.

Cinq automatismes conditionnent donc l'ouverture du palier à 12 €, et ils sont à
construire avant le premier client payant, pas après le vingtième :

1. **Provisioning** par Terraform et cloud-init, sans jamais ouvrir une session SSH.
2. **Mises à jour** par pull d'image sur minuterie, avec `unattended-upgrades` en
   dessous. Le VPS est jetable, il ne se répare pas à la main.
3. **Sauvegarde** nocturne par `VACUUM INTO` puis envoi vers le stockage objet,
   rétention 30 jours.
4. **Métriques** poussées vers un Prometheus central : le pull ne passera pas sur
   deux cents adresses.
5. **Restauration** complète depuis zéro, sous forme d'un script déjà exécuté au
   moins une fois pour de vrai.

Si les cinq ne sont pas vrais, ne vendez pas le palier à 12 € : commencez à 39 €,
où une intervention manuelle reste payable.

### Le chantier bloquant : sortir les blobs vers l'objet

C'est le chemin critique de tout ce document. `STORAGES["default"]` est un
`FileSystemStorage` et le code appelle `default_storage.path()`, qui lève
`NotImplementedError` sur tout backend objet. Le périmètre réel est cependant
contenu - hors tests et hors migrations déjà appliquées :

| Fichier | Ce qui accroche |
|---|---|
| `files/services/_storage_ops.py` | 11 appels à `.path()` - l'essentiel du travail |
| `files/webdav/resources.py` | 1 appel, dans la copie de ressource |
| `files/sync.py` | `os.scandir` pour la réconciliation disque / base |
| `chat/management/commands/purge_orphan_attachments.py` | `os.scandir` sur l'arborescence des pièces jointes |

La difficulté n'est pas mécanique, elle est conceptuelle : **le stockage objet
n'a pas de répertoires.** Renommer un dossier ne peut pas rester une opération
de système de fichiers.

**Décision : la clé de stockage reste le chemin logique.** Le bucket et le
disque reflètent exactement l'arborescence du workspace, comme le fait Nextcloud.
Un `rclone` du bucket rend un workspace lisible, la donnée survit à la perte de
la base, et le support peut regarder le stockage et comprendre. Un bucket en
UUID n'offre aucune de ces trois garanties. Le coût de ce choix est assumé et
gérable :

- **OVHcloud ne facture ni les requêtes API, ni l'entrée, ni la sortie** sur
  l'Object Storage. Un déplacement de dossier en O(n) copies est donc un problème
  de latence, pas de facture.
- **Les dossiers vides ont besoin d'un objet marqueur** à clé terminée par `/` -
  la convention que tous les navigateurs S3 comprennent.
- **L'ordre des opérations doit s'inverser** par rapport au code actuel : copier
  toutes les clés, puis basculer les lignes en base dans une transaction, puis
  supprimer les sources. Aujourd'hui la base est réécrite en premier, ce qui est
  correct devant un `os.rename` atomique et faux devant dix mille copies S3.
- **Au-delà d'un millier d'objets, le déplacement part en tâche de fond.** La
  bascule en base reste synchrone, le miroir converge derrière.
- **Une commande d'audit** vérifie que `content.name` vaut bien le chemin attendu
  pour chaque ligne, et répare les écarts. C'est elle qui fait du miroir une
  garantie vérifiable plutôt qu'une intention - rien ne contrôle cet invariant
  aujourd'hui, même sur le système de fichiers.

Effet de bord bienvenu, et il est important : **disque local et objet partagent
la même disposition**, donc la donnée d'un client se déplace de l'un à l'autre
par simple synchronisation, sans transformation. Le choix du stockage devient un
drapeau de déploiement, et la montée d'un palier d'hébergement à l'autre cesse
d'être une migration.

Un piège à traiter en même temps : `_relocate_on_storage` retombe sur
`_relocate_without_paths` quand le backend n'a pas de `path()`, or ce fallback ne
sait traiter qu'un objet unique. Sur un dossier il renvoie False sans rien faire,
**après** que la base a déjà été repointée sur le nouveau préfixe. Inoffensif
aujourd'hui puisque le backend est toujours un système de fichiers ; activé le
jour de la bascule.

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
