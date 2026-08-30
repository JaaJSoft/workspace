# Analyse concurrentielle des prix - suites collaboratives auto-hébergées

Objectif : situer Workspace sur le marché et définir une grille tarifaire par
serveur / par utilisateur qui soit compétitive.

Toutes les valeurs sont **HT**, converties à 1 USD ≈ 0,92 EUR quand la source
est en dollars. Les pages tarifaires de plusieurs éditeurs
(nextcloud.com, egroupware.org, grommunio.com) sont inaccessibles depuis
l'environnement de recherche : ces montants viennent de sources secondaires et
d'agrégateurs, et sont à re-vérifier sur les pages officielles avant toute
communication publique.

## 1. Trois modèles de tarification coexistent

| Modèle | Qui l'applique | Ce qu'il facture |
|---|---|---|
| **Par utilisateur / boîte** | Nextcloud, ownCloud, Seafile, grommunio, Zimbra, Open-Xchange, BlueMind, Carbonio | Le siège, souvent avec un plancher (25 ou 100 sièges) |
| **Par serveur / instance** | Cloudron, Plesk, Proxmox, Unraid | La machine, utilisateurs illimités |
| **Par palier d'utilisateurs, perpétuel** | Bitrix24 On-Premise | Une licence à vie + ~25 %/an de maintenance |

Le marché de l'auto-hébergé collaboratif est **massivement au siège**. Le
per-serveur existe surtout côté infrastructure (hyperviseur, panneau de
contrôle, PaaS) - c'est précisément l'angle mort dans lequel Workspace peut
se placer.

## 2. Concurrents auto-hébergés

| Produit | Prix | Contraintes |
|---|---|---|
| Nextcloud Community | **0 €**, utilisateurs illimités | Aucun support |
| Nextcloud Enterprise | **67,89 € / 99,99 € / 195 €** par user/an (Standard / Premium / Ultimate) | **Minimum 100 sièges** → plancher ≈ 6 800 €/an |
| ownCloud Standard | ≈ 6,99 $/user/mois (~77 €/an) | Minimum 25 sièges, engagement annuel |
| Seafile Pro | **Gratuit ≤ 3 users**, 100 €/an ≤ 9 users, puis **44 €/user/an** | Stockage/sync uniquement |
| grommunio | **à partir de 1,99 €/boîte/mois** (~24 €/an) | Toutes les fonctions dans tous les plans, seul le support varie |
| EGroupware | **eFlat 22,50 €/user/an** on-premise ; eCloud 2,25 €/user/mois | Modèles "utilisateurs actifs" ou "connexions simultanées" |
| Zimbra Network Edition | ≈ **28 $/boîte/an** (Professional, ~150 boîtes) ; 14 $ secteur public | Vendu par packs de 25 |
| Open-Xchange App Suite | ≈ **2,99 $/boîte/mois** prix public conseillé (~33 €/an) | Tarif dégressif pour hébergeurs, sur mesure > 1 000 |
| BlueMind | ≈ **2,10 à 3,45 €/user/mois** (25-41 €/an) | On-premise ou SaaS, positionnement souveraineté FR |
| Zextras Carbonio | CE gratuite ; édition payante ≈ 8,75 $/user/mois selon agrégateurs | Chiffre non confirmé par l'éditeur |
| Rocket.Chat | **Gratuit ≤ 50 users** (Starter) ; Pro 8 $/user/mois | Plan Pro fermé aux nouveaux clients depuis avril 2026 |
| Mattermost | Sur devis, prépayé annuel par utilisateur actif | Professional plafonné à 250 users |
| Bitrix24 On-Premise | **3 590 $ / 50 users**, 24 990 $ / 500 users, 21 242 $ / 1 000 users (Enterprise) | Perpétuel + ~25 %/an |

## 3. Modèles par serveur (les précédents à copier)

| Produit | Prix | Unité |
|---|---|---|
| Cloudron | **15 €/mois** (1 serveur, apps illimitées) · 30 €/mois (multi-serveurs) | Serveur → **180-360 €/an** |
| Plesk | Web Admin 18 $ · Web Pro 28 $ · Web Host 50 $ / mois | Serveur → 216-600 $/an, palier au nombre de domaines |
| Proxmox VE | 120 € / 370 € / 550 € / 1 100 € par an | **Socket CPU**, niveau = SLA de support |

Enseignement : quand on facture au serveur, le palier ne porte pas sur les
fonctions mais sur une **métrique de taille** (domaines chez Plesk, sockets chez
Proxmox) et sur le **niveau de support**. Personne ne bride les fonctionnalités.

## 4. Le plafond SaaS (ce à quoi le client compare)

| Suite | Prix par user/mois (engagement annuel) |
|---|---|
| Microsoft 365 Business Standard | **14 $** (depuis le 1er juillet 2026) · Premium 22 $ |
| Google Workspace Business Standard | **14 $** (16,80 $ en mensuel) |
| Proton Workspace | Mail Essentials 6,99 $ · Standard **12,99 $** · Premium 19,99 $ |
| Infomaniak kSuite | Standard 1,90 € · Pro 7,90 € · Entreprise **12,42 €** |
| Twake Workplace (Linagora) | Standard **4 €** · palier supérieur 12 € |
| Zoho Workplace | Standard 3 $ · Professional 6 $ |
| Odoo | One App Free (users illimités) · Standard ~24,90 $ · Custom ~49 $ |
| Talkspirit | 4 € |

## 5. Lecture du marché

1. **Le socle est gratuit et le restera.** Nextcloud CE, Seafile CE, Carbonio CE,
   mailcow : impossible de faire payer la fonctionnalité de base en
   auto-hébergé. Ce qui se vend, c'est le **support, la garantie et le confort
   d'exploitation**.
2. **Le plancher de sièges est le point de douleur du marché.** Les 100 sièges
   minimum de Nextcloud Enterprise mettent le ticket d'entrée à ~6 800 €/an, ce
   qui exclut de fait toute équipe de 10 à 80 personnes prête à payer. C'est le
   segment le plus mal servi du marché, et c'est le coin d'entrée le plus net.
3. **La zone de prix crédible en auto-hébergé est 20-50 €/user/an.** grommunio
   (24 €), EGroupware (22,50 €), Zimbra (~26 €), Open-Xchange (~33 €), Seafile
   (44 €). Nextcloud à 68-195 € est un cas isolé, porté par sa notoriété.
4. **L'architecture instance-per-tenant de Workspace est un argument
   tarifaire**, pas seulement technique : une instance = un client = une licence.
   C'est mesurable, difficile à contourner, et ça correspond à ce que le client
   installe réellement.
5. **Un per-serveur pur plafonne le revenu.** Un client de 2 000 personnes sur
   une seule instance paierait le même prix qu'une équipe de 15. Il faut un
   plafond de sièges par palier.

## 6. Recommandation

### 6.1 Modèle : licence par instance, paliers au nombre d'utilisateurs

Une ligne de facture par serveur, un palier déterminé par le nombre
d'utilisateurs actifs. Le client achète « mon instance », pas « 43 sièges » -
c'est le discours qui gagne contre Nextcloud, tout en gardant une progression du
revenu avec la taille du compte.

**Aucun palier ne bride une fonctionnalité.** Comme chez grommunio et Proxmox,
ce qui varie est le support et la garantie. C'est plus simple à vendre, plus
simple à coder, et ça évite le procès en « open core mutilé ».

### 6.2 Grille proposée (auto-hébergé, HT, engagement annuel)

| Palier | Périmètre | Prix | € / user / an au plafond |
|---|---|---|---|
| **Community** | 1 instance, users illimités, sans support | **0 €** | - |
| **Homelab** | 1 instance, ≤ 5 users, support best-effort | **49 €/an** | 9,80 € |
| **Starter** | 1 instance, ≤ 25 users, support e-mail 3 j ouvrés | **290 €/an** | 11,60 € |
| **Team** | 1 instance, ≤ 100 users, support 1 j ouvré, mises à jour testées | **890 €/an** | 8,90 € |
| **Business** | 1 instance, ≤ 250 users, SLA 4 h ouvrées, accompagnement montée de version | **1 890 €/an** | 7,56 € |
| **Enterprise** | Instances multiples, users illimités, SLA contractuel, support d'installation | **à partir de 4 900 €/an** | ≈ 5-10 € selon volume |
| **Hébergeur / MSP** | Revente en marque blanche | **0,60 à 0,90 €/boîte/mois** (7-11 €/user/an), engagement volume | - |

### 6.3 Pourquoi ces montants

- **Le prix affiché est le prix par instance, pas par siège.** « 890 €/an pour
  toute mon équipe » se compare à « 6 800 €/an minimum chez Nextcloud » : c'est
  un facteur 7,6 sur le ticket d'entrée du segment 25-100 personnes.
- **Le prix par siège implicite (7 à 12 €/user/an) reste sous tous les
  concurrents commerciaux** - environ moitié moins que grommunio et EGroupware,
  un quart de Seafile Pro, un dixième de Nextcloud Standard. C'est le
  positionnement d'un entrant sans notoriété : on achète la référence client,
  pas la marge.
- **Le tarif MSP à 0,60-0,90 €/boîte/mois passe sous grommunio (1,99 €) et sous
  le prix public conseillé d'Open-Xchange (2,99 $)**, ce qui rend l'offre
  réellement attractive pour un hébergeur qui revend - le canal le plus rentable
  pour un produit instance-per-tenant.
- **Homelab à 49 €/an** existe pour transformer l'audience auto-hébergeur en
  base installée payante, à un prix d'achat d'impulsion. C'est le rôle que joue
  Unraid ou Cloudron sur ce segment.

### 6.4 Si une offre SaaS est ouverte plus tard

Se placer entre Infomaniak Standard (1,90 €) et Pro (7,90 €), donc très en
dessous de Google et Microsoft (14 $) :

| Offre | Prix | Contenu |
|---|---|---|
| Essentiel | **2,90 €/user/mois** | 50 Go/user |
| Pro | **4,90 €/user/mois** | 250 Go/user, sauvegardes, SLA |
| Souverain | **7,90 €/user/mois** | Instance dédiée, hébergement au choix, SLA renforcé |

### 6.5 Points de vigilance

- **Contrôler le contournement.** Sans plafond de sièges appliqué par la clé de
  licence, un client de 500 personnes achète le palier Starter. Le comptage doit
  porter sur les **utilisateurs actifs** (connectés sur 30 jours glissants),
  métrique standard chez Mattermost et EGroupware.
- **Trancher la licence du cœur avant d'annoncer un prix.** AGPL pur (le support
  est le seul produit, modèle grommunio) ou open core (des modules Enterprise
  fermés, modèle Nextcloud). La grille ci-dessus suppose le premier cas.
- **Le coût réel, c'est le support.** À 890 €/an, deux tickets complexes par an
  consomment la marge. Le SLA affiché doit être tenable en solo : 3 jours
  ouvrés sur Starter n'est pas une faiblesse commerciale, c'est ce qui rend le
  prix soutenable.
- **Ces prix sont une position d'entrée sur 18 à 24 mois.** Avec des références
  clients, le palier Team a vocation à monter vers 1 500-2 000 €/an, ce qui
  reste très en dessous de Nextcloud.

## Sources

- Nextcloud : https://www.trustradius.com/products/nextcloud/pricing · https://www.capterra.com/p/161572/NextCloud/pricing/
- EGroupware : https://www.egroupware.org/en/pricing · https://www.trustradius.com/products/egroupware/pricing
- grommunio : https://grommunio.com/pricing/
- Zimbra : https://wiki.xmission.com/Purchasing_Zimbra_Licensing_and_Support
- Seafile : https://www.seafile.com/en/pricing/ · https://forum.seafile.com/t/pro-edition-pricing/2430
- ownCloud : https://owncloud.com/pricing-copy-2/ · https://www.itqlick.com/owncloud/pricing
- Open-Xchange : https://www.open-xchange.com/open-xchange-hosting-edition · https://blog.whmcs.com/133656/feature-spotlight-ox-app-suite
- BlueMind : https://www.comparatif-logiciels.fr/logiciel/avis-bluemind/ · https://www.bluemind.net/
- Carbonio : https://www.trustradius.com/products/zextras-carbonio/pricing
- Bitrix24 : https://www.bitrix24.com/prices/ · https://atevisystems.com/buy/bitrix24-on-premise/
- Rocket.Chat / Mattermost : https://www.itqlick.com/rocketchat/pricing · https://ossalt.com/guides/mattermost-vs-rocketchat-2026
- Cloudron : https://www.cloudron.io/pricing.html
- Plesk : https://www.plesk.com/pricing/ · https://costbench.com/software/cloud-infrastructure/plesk/
- Proxmox : https://cloud-pve.com/proxmox-ve-subscriptions/ · https://wz-it.com/en/knowledge/proxmox/how-much-does-proxmox-cost/
- Microsoft 365 : https://redriver.com/collaboration/microsoft-365-price-increase-2026
- Google Workspace : https://workspace.google.com/pricing · https://www.name.com/blog/google-workspace-pricing
- Proton : https://www.stackscored.com/pricing/workspace-suites/proton-business/
- Infomaniak kSuite : https://www.infomaniak.com/en/support/faq/80/discover-ksuite-and-its-products · https://infoswitch.fr/en/blog/infomaniak-pricing-2026-all-services
- Twake Workplace : https://linagora.com/en/twake-workplace · https://souverain.ovh/twake-workplace-alternative-google-workspace-microsoft-365/
- Zoho Workplace : https://www.zoho.com/workplace/pricing.html
- Odoo : https://www.erpresearch.com/pricing/odoo
- Talkspirit / Wimi / Whaller : https://www.talkspirit.com/best-software/best-french-collaborative-tools
