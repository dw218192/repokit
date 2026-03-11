# Coding Standards — AI Anti-Patterns

These are common anti-patterns that AI-generated code tends to introduce.
Reviewers must reject code that exhibits any of these.

## 1. Defensive Fallbacks

Don't wrap code in error handlers "just in case." If something should work, let
it fail visibly when it doesn't.

- Don't catch import/include/require errors and fall back to a stub or nil.
  If the dependency is required, let the failure surface.
- Don't use fallback accessors (`getattr(obj, "x", default)`, optional
  chaining `?.`, `?? fallback`, `|| default`) on values that must exist.
  Access them directly — crash if the contract is violated.
- Don't guard against null/nil/None/undefined when the value is guaranteed by
  the API contract. Unnecessary null checks hide real bugs by silently
  skipping code that should have run.

## 2. Error Hiding

Never swallow errors silently. Catch-all handlers that discard or merely log
errors hide real bugs from the developer.

- No bare catch-all blocks that do nothing (`catch { }`, `except: pass`,
  `rescue => nil`).
- No broad catches that log-and-continue — the caller thinks it succeeded.
- Catch only specific, expected error types. Let unexpected errors propagate.
- Only handle errors at system boundaries (user input, network, file I/O) —
  not around internal logic that should always succeed.

## 3. Unnecessary Abstractions

Don't create helpers, base classes, wrappers, or factory functions for one-time
operations. Three similar lines of code are better than a premature
abstraction. Don't design for hypothetical future requirements.

## 4. Compatibility Shims

Don't add backward-compatibility code unless explicitly requested. No old-name
aliases, no deprecated re-exports, no version-checking conditionals when the
project has a fixed minimum version.
