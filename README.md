# skeetio

Renders a Bluesky post to a 9:16 short.

Give it a post URL. It fetches the text and the author's avatar, builds a small
posable creature that wears that avatar as its face, stands it in front of
public-domain archival footage, sets the post in large serif type, and muxes the
whole thing to an mp4 that Bluesky, Shorts, Reels and TikTok will all accept.

The audio is the archival clip's own soundtrack.

```
python3 render/make_video.py \
  --post https://bsky.app/profile/someone.bsky.social/post/3kabc... \
  --out out.mp4
```

---

## A position on consent, up front

This tool makes derivative works of other people's writing and other people's
faces. That is worth being deliberate about, so:

**Do not let a bot built on this initiate a permission conversation.** An
automated system contacting a stranger to obtain something from them is the
shape of extraction, and a friendly payload does not change the shape. It is
also self-defeating: if the ask carries the finished render, then the derivative
work has already been published into that person's mentions before they agreed
to anything. You cannot ask permission to publish by publishing.

The shape that works is the inverse — **only ever render for people who asked
you to.** Someone requests a video of their own post, and consent is inherent in
the request: no gesture to interpret, no like to poll, no revocation ambiguity,
and no likeness question, because they handed you their own avatar.

If you want third-party nomination, return the render **to the nominator** and
let them share it with the author themselves. A person telling their friend
"look what I made of your post" is ordinary; the identical message from a bot is
solicitation.

Two things worth knowing if you go further than that:

- **A profile picture is frequently not the account holder's to license.**
  Commissioned avatars and professional headshots are everywhere, and the person
  you are asking may hold only the right to use the image *as an avatar*. They
  can say yes in complete good faith and still not have the right they granted.
- `--generic` renders the creature with the author's *palette* and no avatar at
  all. That is personalised without being a likeness, and it is what you send
  when you have no permission yet.

## Install

Needs Python 3.10+ and `ffmpeg` **and `ffprobe`** on PATH (both ship together in every standard ffmpeg distribution).

```
pip install -r requirements.txt
```

## Usage

Render a post over a specific archival clip:

```
python3 render/make_video.py --post <bsky url> --clip Designfo1956 --start 118 --out out.mp4
```

Useful flags:

| flag | effect |
| --- | --- |
| `--variant face` | the avatar *is* the creature's head (default) |
| `--variant belly` | the creature keeps its own eyes, avatar rides on its belly |
| `--generic` | palette-derived creature, no avatar — no likeness used |
| `--point` | raises an arm toward the text |
| `--silent` | drop the archival audio bed |
| `--variant crab` | the pfp becomes a crab's carapace, eyestalks and all |
| `--dur` / `--fps` | length and frame rate (default 10s, 24fps) |

Build your own b-roll library:

```
python3 render/curate.py --collection prelinger --rows 90 --keep 45 --out assets/broll-prelinger.json
python3 render/curate.py --collection nasa --rows 60 --keep 30 --out assets/broll-nasa.json
```

Upload to Bluesky as a native video blob (this **does not post** — it uploads
and stages the record; `--create-post` is opt-in):

```
python3 render/publish.py --video out.mp4 --env path/to/.env --alt "..."
```

The `.env` needs two keys:

```
BSKY_IDENTIFIER=you.bsky.social
BSKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Use an [app password](https://bsky.app/settings/app-passwords), never your account password.

## Where the footage comes from

[Prelinger Archives](https://archive.org/details/prelinger) on the Internet
Archive: 10,460 items, all `creativecommons.org/licenses/publicdomain/`, direct
mp4 URLs, no API key. NASA adds another 13,728, public domain by statute.

`curate.py` vets candidates by streaming a handful of frames straight off
archive.org rather than downloading each film, and scores them on the things
that actually matter for compositing:

- **motion** — does the shot go anywhere
- **detail** — edge energy, as a proxy for eye-catching
- **brightness fit** — dark plates die under a scrim, bright ones eat the type
- **floor calm** — the bottom third carries the figure, so it should be quiet there

It deliberately does **not** score on colour. The earnest mid-century monochrome
instructional films are most of the charm and should not be penalised for being
grey.

### It also screens for subject, and you should not remove that

The scorer selects *for* propaganda. It rewards motion, edge detail, brightness
and a quiet lower third — and mid-century propaganda is well made: steady
camera, high production value, busy frames. The first library this project
built led with the US government's 1943 film justifying Japanese-American
internment, and pairing is uniform random, with the author's real name and face
on screen.

So `curate.py` carries two tiers. `BLOCK` covers material no pairing defends.
`HOLD` names the judgment calls and keeps them out until you admit them
deliberately — silently dropping a camp artifact or a period travelogue is its
own kind of wrong. A keyword screen cannot tell a film that *is* racist from a
1947 US Army film *against* racism, so it does not try; it defers.

Curated pools are not safe by construction. They are safe once a human has
read the list.

It also gates on licence. Every rendered frame stamps "public domain", so a
clip whose licence does not actually say that makes the credit assert a false
legal fact — and a non-commercial term is wrong for a monetised channel
specifically.

## Things learned the hard way

Kept here because they cost time and are not obvious.

**A floating avatar disc cannot nod.** The first version put the profile picture
in a circle and translated it up and down. A disc can only translate, so a nod is
a few pixels of vertical travel that read as a compression artifact at any size.
Giving the avatar a *body* is what fixed it — a body can bob, lean, squash, and
it has an arm to point with. `figure.py`.

**The background does not have to be relevant, it has to move.** This is the
karaoke-video principle: motion holds the eye long enough to finish reading.
Pairing is therefore random by default (`pair.py`), seeded off the post URI so
the same post always draws the same clip. A matcher that always lands on the nose
stops being funny by the fourth video; a coincidence is what people screenshot.

**Text and author must never be separable.** An earlier version took `--handle`
and `--text` as independent arguments and promptly put one person's words on
screen under another person's name. `post.py` now returns text, author and avatar
as one record and there is no way to supply them apart. If you fork this, keep
that property.

**Secondary motion is most of the liveness.** The antenna is driven by the
*derivative* of the nod, so it trails the head and overshoots when the head
stops. It is a handful of lines and it does more than everything else combined.

**Three Bluesky video tokens, three shapes.** `getUploadLimits` needs a
service-auth token whose `lxm` names itself; `uploadVideo` needs one audienced at
**your own PDS**, not the video service, because it writes the blob to your repo
on your behalf; and re-uploading identical bytes returns `409 already_exists`
carrying the completed job, which is a success wearing a failure's clothes.

**A shared enum is not a seam.** This started as "add a sixth value to an
existing renderer's `--look` flag." That renderer turned out to be 16:9
throughout, with the dimensions threaded through its layout and muxer, and no
text-wrapping helper at all — and post text *is* the content here. Check whether
the thing you want to vary is genuinely a parameter or is baked into the
geometry.

## Layout notes

Output is 1080×1920. Content stays inside a safe box that clears the Shorts
chrome — title bar, right-hand action rail, progress bar. Text under the player
UI is the most common way an otherwise-fine short reads amateur.

Type size is chosen by binary search so a six-word post and a sixty-word post
both fill the frame (`skeet_frame.fit_text`).

## Credits and licences

- Footage: [Prelinger Archives](https://archive.org/details/prelinger) and NASA
  via the Internet Archive, public domain.
- [Inter](https://rsms.me/inter/) by Rasmus Andersson — SIL Open Font License,
  see `assets/fonts/OFL-Inter.txt`.
- [EB Garamond](https://github.com/octaviopardo/EBGaramond12) by Octavio Pardo —
  SIL Open Font License, see `assets/fonts/OFL-EBGaramond.txt`.
- Code: MIT, see `LICENSE`.

Rendered videos credit their source clip on-screen. Public domain imposes no
attribution duty, but naming the source is free and it keeps the output legible
as something made rather than something scraped.
