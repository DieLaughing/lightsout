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
| The highlight (tab bar and every switch) | Liquid pill: the leading edge reaches the new option first, the trailing edge follows, small overshoot | 380 ms. `cubic-bezier(.45, 0, .55, 1)`, then `cubic-bezier(.3, 1.3, .5, 1)` |
| Labels and icons being picked | Colour cross-fade | 160 ms |
| Toast | Springs out from the middle, shrinks away | 450 ms `cubic-bezier(.3, 1.35, .5, 1)` in, 200 ms out |
| Loading | "Loading…" only after 350 ms. An in-page switch keeps the dimmed old content instead | |
| Opening something from its card (Shots on Goal: a game card opens the game page) | The card grows into the page's header and shrinks back on the way back (View Transitions); the rest cross-fades, no Rise | 340 ms `cubic-bezier(.2, .9, .3, 1)`, page cross-fade 200 ms |

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
  transition name behind.

Anything new that moves is also tried by eye on staging, in Chromium and in a Firefox-based browser.
