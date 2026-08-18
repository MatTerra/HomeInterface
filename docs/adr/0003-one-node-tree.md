# 3. One node tree, no screen list

Date: 2026-08-18

## Status

Accepted

## Context

The hand-built shells own their navigation in code: the alt shell renders a
bar of tabs as chrome, below the content rectangle, and the app keeps a list
of screens with the bar bound to it.

A dashboard file needs to express navigation too. The obvious port is to keep
both structures - a `screens:` list for the panes, and a tabs bar that the
shell draws around them.

But a tab bar is a layout: it divides space and shows one child at a time.
Treating it as chrome makes it the one layout an author cannot place, size,
remove or nest, purely because of where the old code drew it.

## Decision

A dashboard file is one tree with one `root:`. There is no `screens:` list.

Tabs are an ordinary container: its panes are what used to be screens. A
dashboard that wants a tab bar declares one at root; a dashboard that does
not, does not have one.

Any node may carry an `id:`, and `id` is what navigation targets.

The title bar and the alert footer remain chrome. They are status, not
navigation, and they survive every transition.

## Consequences

`start_screen` becomes `start:`, naming a node id rather than a screen class.

Tab-cycling and the number shortcuts bind to whichever tabs container sits at
root. A dashboard without one has nothing to cycle, and those keys do
nothing.

Navigation targets are ids in a tree rather than positions in a list, so
adding a pane cannot silently renumber another dashboard's shortcuts.

The stock and alt shells keep their own code path. This decision governs the
custom shell only.
