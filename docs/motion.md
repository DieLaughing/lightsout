# Motion: Lights Out and Shots on Goal

How the two apps move. This file is identical in both repos: change it in both in the same session.
`tools/motioncheck.py` checks the rules that can be measured (CI runs it on every push).

## Principles

1. **Motion explains a change**: where you went, what you picked. It is never decoration.
2. **If it could be mistaken for a bug, it's wrong.** A small push or bounce that nobody asked for reads as a glitch.
3. **Speed comes first.** Content is in place at once and animations play on top of it. No animation waits for data,
   and no data waits for an animation (a re-render waits only for a running animation to finish, never longer).
4. **Everything has an off switch.** Settings > Display > animations off, or the phone's Reduce Motion, means no
   movement anywhere (`motionOn()`).

## What moves and what doesn't

| Change | Motion | Timing |
| --- | --- | --- |
| Opening a different page (a tab, a game, a driver, a team) | Rise (default) or Slapshot, per device in Settings > Display | Rise: the page's blocks fade up 14 px, 340 ms each, 35 ms apart (first 8 stagger), the heading un-blurs. Slapshot: 280 ms slide with a small overshoot |
| Switching within a page (series, tab, filter, class, Standings / Leaders) | **The page holds perfectly still**: scroll position, heading and switches don't move. Only the changing part cross-fades | Dims to 45% while its data loads, fades back in 200 ms. No transform |
| Live and quiet refreshes | None: idiomorph changes only what moved | |
| The highlight (every switch, the league switch in the menu too) | Liquid pill: the leading edge reaches the new option first, the trailing edge follows, small overshoot | 380 ms. `cubic-bezier(.45, 0, .55, 1)`, then `cubic-bezier(.3, 1.3, .5, 1)` |
| Labels and icons being picked | Colour cross-fade | 160 ms |
| Toast | Springs out from the middle, shrinks away | 450 ms `cubic-bezier(.3, 1.35, .5, 1)` in, 200 ms out |
| Loading | "Loading…" only after 350 ms. An in-page switch keeps the dimmed old content instead | |
| A goal arrives while you're watching (Shots on Goal) | The number rolls up 26 px and a red beacon sweeps what it's on: the jumbotron, the score strip once that's scrolled away, or the game's card. Never when a score goes down, or on a page that just opened | Roll 340 ms, lamp 1.6 s (the one thing allowed past 600 ms: it's a celebration, and nothing waits for it) |
| Replay (Shots on Goal) | Shots fill in on the rink under the puck you're dragging, or over about 20 s when you press play, with a 600 ms beat and the lamp on each goal. You started it, so the time limit doesn't apply; the page holds still and a touch stops it | |
| Game story (Shots on Goal) | Swiping moves the moments; the jumbotron holds still, its number rolls and the lamp sweeps on each goal you reach going forward, never going back. You started it, so the time limit doesn't apply | Roll and lamp as for a goal |
| Menu (Shots on Goal) | Opening: the menu fades in and its pages rise 14 px into place, 30 ms apart; the button's two lines turn into a cross. Closing: fades out. The page under it holds still and can't scroll | Fade in 200 ms, rise 300 ms `cubic-bezier(.2, .9, .3, 1)`, fade out 160 ms, lines 200 ms |
| Menu bar (Shots on Goal) | Scrolling down slides it off its edge (up, or down when it's at the bottom); any scroll up brings it back | 200 ms |
| House lights (Shots on Goal) | The page changes colour at once; only the puck on the ruler cross-fades | 160 ms |
| Dim at puck drop (Shots on Goal) | When your team's game starts (or ends) the whole screen cross-fades down to Lights out (or back up), like the arena; a toast says why. Turning it on with no game on plays it once as a preview | 1400 ms ease-in-out (view transition); none without one or with motion off |
| Game cards / list (Shots on Goal) | Settings > Display swaps Tonight between team-colour cards and the compact list at once, no motion | none |
| Live dock (Shots on Goal) | It appears and leaves with no slide. It fades aside while the game's own card is on screen. The lamp sweeps it on a goal | 160 ms, opacity and 8 px |
| What if? (Shots on Goal) | Table rows glide to their new places, whole pixels, even between tables | 340 ms `cubic-bezier(.3, 1.2, .5, 1)` |
| Flick your picks (Shots on Goal) | The puck follows the finger, then flies to the picked logo; too small a flick springs back; the next game fades in | Fly 260 ms, spring back 260 ms `cubic-bezier(.3, 1.4, .5, 1)`, fade 200 ms |
| Opening something from what you tapped (Shots on Goal: a game card opens the game page; Lights Out: a race, driver, team or series opens its page) | What you tapped grows into the page's header or title and shrinks back on the way back (View Transitions); the rest cross-fades, no Rise | 340 ms `cubic-bezier(.2, .9, .3, 1)`, page cross-fade 200 ms |

## Rules for new UI

- **New switches** use the shared switch classes (Shots on Goal: `.seg`, `.league` with `.on`; Lights Out:
  `.series-pick`, `.tabs`, `.seg` with `.active`), so they get the pill for free. If a tap redraws the page or changes
  the address, mark the switch `data-swaps` so it's treated as an in-page switch.
- **Only animate `transform` and `opacity`** (`filter` only on the page heading). Use whole-pixel translates: scaling or
  sub-pixel movement re-renders text and makes it jump when the animation ends.
- **Hold the layout.** Never use `scrollIntoView` (it also scrolls the page up or down); scroll a row sideways
  yourself. An in-page switch keeps `#view`'s height until the next real page, so a shorter list can't pull the
  scroll position up.
- **DOM watchers that write to the DOM** must write only when something changed, drop their own mutation records
  (`takeRecords()`) and batch their work once per frame. Any class write wakes a MutationObserver, even removing a
  class that isn't there, and one endless loop froze a page once.
- **Durations** stay between 150 and 400 ms; nothing takes more than 600 ms from start to finish. Stagger at most 8
  items.
- **Every animation checks `motionOn()`.**
- **New browser features** are progressive enhancement: use them when the browser has them and motion is on,
  otherwise fall back to the motion above. Nothing may break or look half-finished without them.

## Shared-element page changes (View Transitions)

The card you tapped and the header of the page it opens share one `view-transition-name` (set inline for that one
navigation, cleared when it ends), so the browser morphs one into the other. Supported in Chrome and Edge, Safari 18
and Firefox 144 (Zen and other Firefox builds follow Firefox). Rules:

- Only when the card is on screen, motion is on and the browser has `document.startViewTransition`; otherwise the
  normal page change runs (Rise or Slapshot). Nothing else depends on it.
- The browser keeps showing the old page until the new one is drawn (as the apps already do while a page loads),
  capped at 800 ms: a slower page lands without the morph.
- One name per page at a time; no Rise or Slapshot during a morph.

## Checked

`tools/motioncheck.py` opens the app from fixture data in a phone-sized Chromium and checks that:

- every page opens fresh without freezing or a page error;
- in-page switches, tapped while scrolled down, move nothing by more than 1 px (scroll position, heading, the switch);
- every visible switch's pill ends up under its picked option;
- tapping a card plays its morph (where the browser has View Transitions), lands on the page and leaves no
  transition name behind;
- (Shots on Goal) Replay, What if?, Flick your picks, Game story, the season ribbon, Compare and the live dock work when driven by hand and leave the page as they found it.

Anything new that moves is also tried by eye on staging, in Chromium and in a Firefox-based browser.
