# Product loop

## Goal

Keep Untether a reliable Telegram interface for local coding agents.

## Current bottleneck

Telegram replies select/resume sessions, but selected quote text is not carried into the agent prompt, so instructions such as “change this part” lose their referent.

## Selection rule

Prefer the smallest upstream-compatible transport/domain change that preserves existing reply-resume and project-routing behaviour.

## Oracle

Focused parser/runtime/bridge/API tests, the full upstream test and lint gates, then a live Telegram smoke covering reply and selected quote while preserving topic/session behaviour.

## Stop conditions

Stop on required secret disclosure, publication, destructive migration, broader protocol redesign, inability to preserve resume semantics, or a failing upstream gate that cannot be localized safely.
