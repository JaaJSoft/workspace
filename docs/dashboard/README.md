# Dashboard

The home page (`/`) is where a user lands after signing in: a greeting, a
search box over the whole workspace, a tile per app with what is waiting
there, and the day ahead.

![Dashboard](../images/home.png)

## At a glance

- **Greeting and date**, by the hour of the user's own time zone. When the
  browser shares its location, the current weather follows the date; the
  browser asks [Open-Meteo](https://open-meteo.com/) directly, nothing goes
  through the server.
- **Search**: the same search as the top bar's command palette (`Ctrl+K` /
  `Cmd+K`), with its quick actions.
- **App tiles**: one per app the user can open, with the number of unread
  notifications from it. The count goes down as items are dealt with and
  updates live. A tile with a single pending item opens it directly - the
  unread conversation, the overdue task - and the app's home otherwise.
- **Today**: one list of what needs the user today, each row opening its
  item. It gathers:
  - *Events*: what is still ahead today in the user's calendars;
  - *Tasks*: assigned open tasks that are overdue, due today or urgent;
  - *Mail*: starred messages, and unread ones carrying a label that notifies
    on apply (the default *Urgent* label is one). Trash and Spam stay out;
  - *Invitations*: events the user was invited to and has not answered,
    soonest first.

  Urgent rows lead the list whatever their app: overdue and urgent tasks,
  unread mail with a notifying label. The rest follows in the order above.
  Quick filters above the list narrow it to the urgent rows or to one app,
  each with a count of all it holds; the full list shows its first rows and
  a button for the rest. An item leaves the list once it is dealt with in
  its app, and an app the user cannot open is left out.
- **Recent Activity**: what other people did across the workspace, by app,
  with a search and a refresh button. It only lists apps the user has access
  to.
- **Usage**: the chart button in the top right corner opens the user's storage
  against their quota and their counts of files, messages, mail and events.

## Preferences

The *Dashboard preferences* button (sliders, top right) holds the page's own
settings:

- which sources the Today card lists. With all of them off, the card goes
  away;
- which apps have a tile. A hidden app also leaves the app switcher (`Alt+K`),
  but stays reachable from the search and its links.
