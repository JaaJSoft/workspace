# Workspace - Ideas & Roadmap

## Quick Wins

Améliorations rapides à fort impact sur l'existant.

- [ ] **Drag & drop upload** - Glisser des fichiers depuis l'OS directement dans le browser
- [ ] **Preview PDF** - Viewer PDF intégré (pdf.js)
- [ ] **Preview Markdown** - Rendu Markdown dans le file viewer
- [ ] **Preview code** - Coloration syntaxique (highlight.js / Shiki)
- [ ] **Raccourcis clavier globaux** - Ctrl+K command palette, Ctrl+N nouveau, Suppr, Ctrl+C/V
- [ ] **Taille des dossiers** - Calcul récursif de la taille affichée dans les propriétés
- [ ] **Breadcrumb cliquable dans les propriétés** - Naviguer au parent depuis la modale
- [ ] **Multi-select avec Shift+Click** - Sélection de plage dans le file browser
- [ ] **Download fichier/dossier** - Télécharger un fichier ou un dossier (zip)
- [ ] **Quota utilisateur** - Limite de stockage par user avec jauge dans le dashboard
- [ ] **Dark/Light mode persistant** - Sauvegarder le thème choisi côté serveur
- [ ] **Notifications toast améliorées** - Stack de notifications avec auto-dismiss
- [ ] **Empty states** - Illustrations quand un dossier/vue est vide
- [ ] **Tri persistant** - Mémoriser le tri choisi par l'utilisateur (cookie/DB)
- [ ] **Avatar upload** - Photo de profil utilisateur

---

## Modules Ecosystem

### 1. Notes & Wiki

Un éditeur de documents collaboratif intégré, style Notion/Outline.

- [ ] Éditeur rich text (Tiptap / ProseMirror)
- [ ] Pages hiérarchiques (arbre comme les fichiers)
- [ ] Support Markdown natif
- [ ] Templates de pages (meeting notes, specs, daily standup)
- [ ] Liens entre pages (backlinks / graph)
- [ ] Embed de fichiers depuis le module Files
- [ ] Table des matières auto-générée
- [ ] Export PDF / Markdown
- [ ] Historique de versions (diff visuel)
- [ ] Épingler des pages dans la sidebar

---

### 2. Tasks & Projects (Jira-like)

Gestion de projets et suivi de tâches.

- [ ] Projets avec board Kanban (drag & drop)
- [ ] Vue liste, board, calendrier, timeline (Gantt)
- [ ] Tâches avec titre, description (rich text), assignee, priorité, labels
- [ ] Sous-tâches et checklists
- [ ] Statuts personnalisables par projet
- [ ] Sprints avec dates de début/fin
- [ ] Filtres sauvegardés et vues personnalisées
- [ ] Commentaires sur les tâches
- [ ] Pièces jointes (lien avec module Files)
- [ ] Numérotation auto des tâches (PROJ-123)
- [ ] Estimation de temps et time tracking
- [ ] Dashboard projet (burndown chart, vélocité)
- [ ] Notifications sur assignation et mentions
- [ ] Recurring tasks (tâches récurrentes)

---

### 3. Email Client

Client email intégré pour centraliser la communication.

- [ ] Connexion IMAP/SMTP (multi-comptes)
- [ ] Boîte de réception unifiée
- [ ] Composer, répondre, transférer
- [ ] Pièces jointes -> sauvegarde directe dans Files
- [ ] Labels / tags personnalisés
- [ ] Recherche full-text dans les emails
- [ ] Signatures HTML par compte
- [ ] Snooze / rappels
- [ ] Convertir un email en tâche (lien avec Tasks)
- [ ] Templates d'emails
- [ ] Filtres et règles automatiques

---

### 4. Calendar & Scheduling

Calendrier et planification.

- [ ] Vue jour, semaine, mois
- [ ] Événements avec titre, description, lieu, participants
- [ ] Événements récurrents (RRULE)
- [ ] Sync CalDAV / Google Calendar / Outlook
- [ ] Rappels (email, notification in-app)
- [ ] Créneaux de disponibilité (style Calendly)
- [ ] Lien avec les tâches (deadlines visibles dans le calendrier)
- [ ] Vue agenda (liste chronologique)
- [ ] Fuseaux horaires
- [ ] Invitations et RSVP

---

### 5. Contacts & CRM

Gestion de contacts et relation client.

- [ ] Fiches contacts (nom, email, téléphone, entreprise, notes)
- [ ] Entreprises / organisations
- [ ] Tags et segments
- [ ] Historique des interactions (emails envoyés, meetings, tâches liées)
- [ ] Import/export CSV, vCard
- [ ] Recherche et filtres avancés
- [ ] Pipeline de deals (CRM simplifié)
- [ ] Merge de doublons
- [ ] Champs personnalisés

---

### 6. Chat & Messaging

Communication interne en temps réel.

- [ ] Channels publics et privés
- [ ] Messages directs (1:1 et groupes)
- [ ] Threads de discussion
- [ ] Partage de fichiers (lien avec Files)
- [ ] Réactions emoji
- [ ] Mentions @user et @channel
- [ ] Recherche dans les messages
- [ ] Notifications push (WebSocket)
- [ ] Statut en ligne / absent / occupé
- [ ] Épingler des messages importants
- [ ] Intégration avec Tasks (créer une tâche depuis un message)

---

### 7. Bookmarks & Links

Gestionnaire de favoris et veille.

- [ ] Sauvegarder des URLs avec titre, description, tags
- [ ] Capture automatique du titre et favicon
- [ ] Screenshot/preview de la page
- [ ] Collections / dossiers de bookmarks
- [ ] Import depuis le navigateur (HTML bookmark file)
- [ ] Recherche full-text
- [ ] Partage de collections
- [ ] Détection de liens morts
- [ ] Extension navigateur pour sauvegarder en un clic
- [ ] Lecture offline (archive de la page)

---

### 8. Time Tracking

Suivi du temps de travail.

- [ ] Timer start/stop avec projet et tâche associés
- [ ] Saisie manuelle d'heures
- [ ] Vue timesheet hebdomadaire
- [ ] Rapports par projet, client, période
- [ ] Export CSV / PDF
- [ ] Objectifs hebdomadaires
- [ ] Intégration native avec Tasks (tracker depuis une tâche)
- [ ] Dashboard avec graphiques de répartition
- [ ] Pomodoro timer intégré
- [ ] Facturable vs non-facturable

---

### 9. Passwords & Secrets

Gestionnaire de mots de passe et secrets.

- [ ] Coffre-fort chiffré (AES-256)
- [ ] Entrées : login, mot de passe, URL, notes, TOTP
- [ ] Générateur de mots de passe
- [ ] Catégories et tags
- [ ] Recherche rapide
- [ ] Copie sécurisée dans le presse-papier (auto-clear)
- [ ] Audit log des accès
- [ ] Import depuis Bitwarden, 1Password, KeePass (CSV)
- [ ] Chiffrement côté client (zero-knowledge)
- [ ] Partage sécurisé de secrets (lien temporaire)

---

### 10. Dashboards & Analytics

Tableaux de bord personnalisables.

- [ ] Widgets configurables (stats, graphiques, listes)
- [ ] Dashboard par module (files, tasks, time, etc.)
- [ ] Drag & drop pour organiser les widgets
- [ ] KPIs personnalisés
- [ ] Graphiques (Chart.js / Apache ECharts)
- [ ] Dashboard d'activité globale (feed)
- [ ] Export des rapports
- [ ] Dashboards partagés entre users

---

### 11. Snippets & Code

Gestionnaire de snippets de code.

- [ ] Snippets avec coloration syntaxique
- [ ] Support multi-langages
- [ ] Tags et catégories
- [ ] Recherche full-text dans le code
- [ ] Versioning des snippets
- [ ] Copie en un clic
- [ ] Embed dans les Notes/Wiki
- [ ] Import depuis GitHub Gists
- [ ] Collections partagées
- [ ] Support diff (comparer deux versions)

---

### 12. Forms & Surveys

Création de formulaires et sondages.

- [ ] Builder drag & drop de formulaires
- [ ] Types de champs : texte, choix, date, fichier, note, etc.
- [ ] Logique conditionnelle (afficher si...)
- [ ] Lien partageable (public ou authentifié)
- [ ] Collecte et export des réponses (CSV, JSON)
- [ ] Notifications sur nouvelle réponse
- [ ] Templates de formulaires
- [ ] Intégration avec Tasks (créer une tâche par réponse)
- [ ] Statistiques des réponses

---

## Transversal / Infrastructure

Fonctionnalités partagées entre tous les modules.

### Recherche globale
- [ ] Recherche unifiée across tous les modules (Ctrl+K)
- [ ] Indexation full-text (PostgreSQL FTS ou Meilisearch)
- [ ] Résultats groupés par type (fichier, tâche, note, contact...)
- [ ] Recherche récente et suggestions

### Notifications
- [ ] Centre de notifications in-app
- [ ] WebSocket pour le temps réel
- [ ] Préférences de notification par module
- [ ] Email digest (quotidien/hebdomadaire)
- [ ] Notification push (PWA)

### Utilisateurs & Teams
- [ ] Profils utilisateurs enrichis
- [ ] Teams / groupes
- [ ] Rôles et permissions par module
- [ ] Invitation par email
- [ ] SSO (SAML, OAuth2 - Google, GitHub, Microsoft)
- [ ] 2FA (TOTP)
- [ ] Audit log global

### API & Intégrations
- [ ] Webhooks configurables
- [ ] API tokens personnels
- [ ] Zapier / n8n / Make integration
- [ ] Import/export global (JSON)
- [ ] CLI pour interactions automatisées
- [ ] SDK Python/JS

### UI/UX
- [ ] PWA (Progressive Web App) - installable sur desktop/mobile
- [ ] Responsive design mobile
- [ ] Sidebar modulaire (chaque module = une section)
- [ ] Thèmes personnalisables
- [ ] Onboarding wizard pour les nouveaux utilisateurs
- [ ] Mode focus (masquer la sidebar)
- [ ] Raccourcis clavier par module
- [ ] i18n (FR, EN minimum)
