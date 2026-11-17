# Face grouping

How the People tab of [Photos](README.md) finds, groups and corrects faces:
the rules behind what users see, for whoever changes the code or wonders why
a face landed where it did. Setting it up is in the
[README](README.md#face-detection); the [face bench](face-bench.md) and the
[appearance bench](appearance-bench.md) measure what is described here.

## Faces in videos

Videos are read where ffmpeg and ffprobe are installed (the Docker image ships
them); without them, videos are simply left out and never count as pending.
Each video runs in its own low-priority task, so a long one never holds up the
photos behind it.

- **Frames**: ffmpeg samples a frame at every scene change and at least every
  `PHOTOS_FACES_VIDEO_INTERVAL` seconds, up to `PHOTOS_FACES_VIDEO_MAX_FRAMES`.
  The faces of each frame are found like a photo's.
- **Tracking**: a face in one frame and a face in the next one are the same
  person when their boxes overlap and they look much alike. Someone who leaves
  and comes back is recognised by their face alone; two faces of one frame are
  always two people.
- **One face per person and video**: the grouping sees each person once per
  video, as it sees them once per photo. Their face picture is the best one of
  the video, and "People in this video" says when it was seen, with a link
  playing the video from there.

The cost is a few seconds of CPU per video on a small server for detection,
plus the decoding: ffmpeg reads the whole video to find its scene changes, so
a long 4K recording costs far more than a short phone clip. Lower
`PHOTOS_FACES_VIDEO_MAX_DURATION` on a server that should not spend that.

## How grouping works

- Right after a photo is analyzed, each new face joins the group its nearest
  neighbours vote for, if they are close enough - and if it is within the
  threshold of the group's centroid too: a face like one wrong guess of a
  group, but not like the group, would otherwise join it and bring its own
  look-alikes after it. A face the user confirmed votes three times as hard
  as a guessed one, and weighs three times as much in its group's centroid.
  A face the model reads
  poorly - blurred, tiny, turned away - joins only on firm evidence: one of
  the group's good or confirmed faces very close to it, or three of its faces within the
  threshold (the blurred photo of someone seen often sits among many of
  their faces without being very close to any). How well the model reads a
  face is, with `yunet_sface` and `yunet_adaface`, the length of its
  embedding before normalization, which these models make shorter the less
  sure they are; with `scrfd_arcface`, whose embedding length says no more
  than its detector does, the detector's confidence discounted for a small
  or blurred face.
- Nightly (and once enough faces wait), the faces still ungrouped are
  clustered. Two faces of one photo are never put in the same group; the
  database refuses it too. Faces read poorly never start a group, and a
  single clear face of someone is a group of its own.
- That rule also tells look-alikes apart. When a group keeps turning away
  faces as close to it as its own - two brothers photographed together, the
  group holding one of them each time - it is regrouped without ever putting
  two faces of one photo together, and the faces that side with the ones it
  turned away leave with them, in a new group.
- Corrections always win: a face the user placed is never moved, and a face
  the user took out ("Not this person", "Ungroup") is never grouped again
  automatically. A face the user hid (a stranger, a poster) is out of the
  grouping too, until unhidden.
- A correction carries the guesses it explains along. When the user takes a
  face out of a group (not this person, someone else, hidden), the faces the
  grouping put there on its own that sit nearer that face than the group's
  centroid - or no longer within the threshold of it - leave too, in the
  background. Each is offered to the grouping again, never to that group:
  most follow the face to the person the user named, the others wait for the
  review. A confirmed face never moves. Undoing the correction puts the
  corrected faces back, not the ones that followed them.

## Naming people

A group is named after a contact from the **People** module: an existing one,
from the user's own address book or a group's, or a new one created from the
typed name. Naming never changes the contact itself, and deleting the contact
only leaves its groups unnamed.

- **One person, several groups.** The same person often spans several groups
  (a child growing up, a beard, glasses); each stays compact, so the grouping
  keeps matching new photos well, and they all show as one person. Saying a
  face is someone ("Change person") puts it in that person's closest group,
  or starts a new one of theirs when it looks like none of them.
- **Two faces of one photo are two people** at the person level too: a photo
  never shows one person twice, whichever of their groups the faces are in.
- **Merging** two groups named after different people asks which name stays.
- **On the contact's page** in People, a *Photos* section lists the photos
  they are in, and *Use as contact photo* sets the contact's avatar from the
  group's cover. Each user only ever sees their own photos: a contact shared
  in a group address book shows every member their own.
- **Search**: a person's name in the global search opens their photos.

## Reviewing people

*Review* on the People tab lists everything the grouping is waiting on, one
card per group of faces, each asking the same question - *who is this?* - and
answered the same way whatever it holds:

- **An unnamed group**, its faces least like the rest first (the others go
  with the answer unseen). A group whose centroid is within the grouping
  threshold of a named person's suggests that person, unless they are
  already in one of its photos.
- **A named person's doubtful faces** (*Is this Léa?*): faces the grouping put
  with them on its own that sit further than half the threshold from their
  group's centroid, most doubtful first. A confirmed face never comes back.
- **Look-alike faces in no group**, taken out of one by the user or never
  grouped (clear ones only: the blurred crowd behind a subject would bury the
  rest), with the named person they look like when there is one - never the
  one they were taken out of.
- **People wearing a named person's clothes** (*Is this Léa?*): people found
  without a usable face whose clothes match Léa's on a photo taken the same
  day, nearest first (see *People without a usable face* below). Saying yes
  puts the photo under Léa; saying no never suggests her for them again.
- **Silhouettes nobody named**: people found without a usable face whose
  clothes suggest nobody, dated photos or not, one card per folder and
  outfit. Those alone in their photo are asked about even on their own
  (the subject of the photo); those who share it only when the folder shows
  the same outfit twice, so the passer-by in the background stays out. A
  card of people alone in their photo asks *Is this Léa?* when Léa is in
  more than half of the folder's photos that show someone named (three at
  least) and one of them is dressed like her in another photo of the
  folder - the folder alone guesses wrong one time in two. Saying yes puts
  the photos under Léa, those left out are never asked about as her again.

A person is in a photo once: someone found by their body cannot be named Léa
when Léa is already another person of that photo - her face is on another
head, or the user said who that other person is. When their box overlaps
hers, they are Léa boxed a second time: the answer goes through, and they
are hidden, as nobody new to ask about. When Léa is elsewhere in the photo,
they are set apart from Léa instead, and hidden: her face was given to the
wrong person, or somebody else was taken for her, and the review never asks
about them again. Léa's face on nobody's head does not count: someone
crouching or bent over wears it lower than a head, and the person asked
about may well be her.

Clicking a face leaves it out of its card's answer; everything else on the
card goes with it. The answer is the guess, a contact or a new name typed in
the card's field, or *Hide* for nobody to name. Faces left out are taken out of the group the answer settles,
or left where they were, and come back in a card of their own. A settled card
leaves the list; the cards settling the most photos come first, 30 at a time.

The review runs from the keyboard: the card at hand has the focus in its name
field, from the first card on and after each answer (on a touch screen, only
after a keyboard answer). Enter takes the guess, or the highlighted contact
once the list is open; the arrows open and walk the contacts, Tab and
Shift+Tab move between cards, Alt+H hides, Escape closes the contacts then
clears the name. Clicking a face hands the keyboard back to its card.

## People without a usable face

Faces miss whoever is seen from behind, turned away, too far or too blurred.
Where the instance also embeds photos
([search and similar photos](README.md#embeddings)), the face analysis is followed by a second pass that finds
people by their body: a person detector (NanoDet-Plus, OpenCV Zoo,
Apache-2.0, 3.8 MB, about 25 ms per photo) boxes everyone, and the torso of
each person wide enough to show their clothes is embedded with the photo
embedding model. No other model is involved: faces keep theirs, everything
else uses the photo model.

People keep their clothes for the day, and rarely longer: a person seen
without a face, or with one too blurred to recognize anyone by, is suggested
as the named person whose torso, on another photo taken the same day or
within 6 hours (a night out goes past midnight), is the nearest - when it is
close enough and clearly closer than anybody else's. Everybody whose face can tell who they are counts as somebody there,
named or not: someone dressed like an unnamed group, or like a face the face
review asks about, is suggested as nobody. Without that, in a library where
few people are named, the stranger sitting by a named person would take
their name. The same goes for a face the review asks to check (*Is this
Léa?*): until the user says it is her, it names nobody by their clothes, or
one wrong guess would name everybody dressed like that person that day. A
person is suggested once per photo, and never when the photo already shows
them, nor when the user said it is not them.

A face is a person's when it sits on their head, or when it is the only
face of the photo on nobody's head and they are its only person with no face
on theirs: someone crouching, bent over or half out of the frame keeps their
name. With as many such faces left as people or more, each of those people
is somebody - never asked about, never suggested - but nobody's clothes.

The detector often boxes someone sitting or half hidden twice: whole, and
from the waist down or one side only. A box nobody named (no face on it, and
the user did not say who it is) is a second box of the person another box of
the photo shows when the two overlap by 0.25 or more (intersection over
union), or by 0.1 with 60% of the smaller one inside the other - a part of
them, their legs or an arm. A second box is never asked about, never
suggested, nobody's clothes, and counted once when the faces left are given
to the people left. Whatever names the first box - its face, the user -
names the person. On the face bench's photos, this takes out three quarters
of those second boxes, and never asks about 6% of the people really seen
without a face (in someone's arms, half behind them); a lower overlap alone
loses one of them for each second box it takes out.

Every correction asks again in the background: around the photos of the
faces corrected, or across the library, half a minute later, when a whole
group is named, merged, hidden or ungrouped.

The suggestions are asked about in the review, never applied on their own,
and a photo is shown under the person once the user said yes.

The thresholds are fitted per embedding model on the face bench's libraries,
on torsos cut from the detector's boxes, for at least 90% precision on any
part of them: there, a quarter to a third of the people with a named person
nearby get a suggestion (36% with SigLIP 2), right 97 to 98% of the time.
scripts/appearance_bench.py measures the method itself.
The `remote` backend has none: with it nobody is found by their body, and no
picture of a person leaves the server.

It is part of face grouping: the same opt-in, the same personal photos (not
videos), the same purge when the user turns it off. Each photo costs the
detector and one embedding per person found: `PHOTOS_APPEARANCES_ENABLED=0`
leaves it out on a host without the CPU to spare. Photos analyzed before it
existed are read by the hourly catch-up (`manage.py catch_up appearances`).

## A person's silhouettes

Once someone found by their body was said to be Léa, her page grows a
*Silhouettes* tab next to *Photos* and *Faces*: everyone the user said is
her in photos where her face does not show. Pick the ones that are someone
else, then *Move to...* another contact with a face in the library, *Not
Léa* (never asked about as her again), or hide them. Each answer can be
undone for a few minutes.

## Correcting several faces at once

The *Faces* tab of a person's page and the hidden faces on the *Hidden* page
are boards of face tiles: click to pick (Shift picks a
range), then act from the bar under them:

- **Move to...** puts the faces with a contact (their closest group, or one
  new group for all the faces that look like none of theirs), a new contact,
  an unnamed person, or someone new with no name yet.
- **That's right** confirms them where they are; **Not *name*** (**Not this
  person** on an unnamed person) takes them out of their group; **Hide** takes them out of the grouping and puts
  them on the *Hidden* page, where **Unhide** hands them back.

A face the correction cannot apply to (its photo already holds that person)
is left as it was, and the toast says so. Every correction can be undone from
its toast for a few minutes, groups it emptied included.
