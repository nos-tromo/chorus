# chorus — AppShell Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt `@infra/ui` v0.9.1-candidate in the chorus SPA — `AppShell`
with the grouped sidebar in its sidebar slot, `PageHeader` rhythm, sign-out
menu, the duplicate version badge and duplicate landing `h1` removed, and
the missing dark-mode accent variant added.

**Architecture:** Plan 5 of the federation rollout (design:
`infra-ui/docs/2026-08-04-app-shell-federation-design.md` in the infra-ui
repo). Frontend-only; chorus is the least-work adoption — it already has
the title+caption rhythm and near-uniform `Card`/`Banner` usage.

**Tech Stack:** React SPA (Vite + TypeScript + Tailwind v4) + `@infra/ui`
(pinned codeload tarball) + vitest.

## Global Constraints

- All frontend commands run inside `frontend/` with pnpm.
- Functionality preserved: nav (incl. conditional `/ingestion` link and the
  `*` → `/` redirect), all tool screens, agent, explorer, i18n en/de parity,
  theme toggle, user display. Sign-out is the ONE addition.
- chorus's i18n keys are dot-nested: `app.header.*` (e.g.
  `app.header.theme.system`).
- Semantic tokens only; no shadows. v0.9.1's `Card` fill change
  (`bg-muted/30` → opaque `bg-muted`) restyles chorus's many Cards — that
  is the intended federation tile look; update only assertions that encoded
  the old class.
- Known accepted limitation: `AppShell` forwards no `menuLabel`; the
  user-menu aria prefix stays "Account" in both locales.
- Confidentiality: synthetic data only; no local machine paths committed.
- Working branch: `feature/app-shell` (controller creates it with this plan
  committed).

---

### Task 1: Bump the `@infra/ui` pin to the v0.9.1 candidate

**Files:**
- Modify: `frontend/package.json:18`
- Modify: `frontend/pnpm-lock.yaml` (via install)

**Interfaces:**
- Produces: v0.9.1-candidate in `node_modules` (`AppShell`, `SidebarGroup`,
  `PageHeader`, `UserMenu`, tile `Card`; `AppHeader` still exported until
  Task 3). The pin targets commit `58ae43a…` — infra-ui PR #36's
  PageHeader banner-role fix; repin to the `v0.9.1` tag once that merges.

- [ ] **Step 1: Bump the pin** — change

```json
"@infra/ui": "https://codeload.github.com/nos-tromo/infra-ui/tar.gz/v0.8.1",
```

to

```json
"@infra/ui": "https://codeload.github.com/nos-tromo/infra-ui/tar.gz/58ae43a498cffc1058e040b0d2b29e0d07f1d941",
```

- [ ] **Step 2: Install and run the existing gates**

```bash
cd frontend && pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

Expected: green, except possibly test assertions that encoded `Card`'s old
`bg-muted/30` — update exactly those to `bg-muted` (intended restyle) and
list them in the commit body.

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml
git commit -m "chore(frontend): pin @infra/ui to the v0.9.1 candidate (infra-ui PR #36)"
```

(plus any test files updated for the Card restyle, listed in the body)

---

### Task 2: i18n key for sign-out

**Files:**
- Modify: `frontend/src/i18n/en.ts` (or wherever `app.header.home` lives)
- Modify: the matching de catalog

- [ ] **Step 1: Add** next to the existing `app.header.*` keys —
  en: `'app.header.sign_out': 'Sign out',` de:
  `'app.header.sign_out': 'Abmelden',`

- [ ] **Step 2: Run the i18n parity test**

Run: `cd frontend && pnpm test src/i18n`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/i18n
git commit -m "feat(frontend): i18n key for the sign-out menu"
```

---

### Task 3: Shell swap — `AppShell` + shared `SidebarGroup`; version deduped; dark accent

**Files:**
- Modify: `frontend/src/layout/Shell.tsx`
- Modify: `frontend/src/layout/Sidebar.tsx`
- Delete: `frontend/src/components/VersionBadge.tsx` (and its test file if
  one exists)
- Modify: `frontend/src/styles/globals.css:6-10`
- Modify: layout test files whose assertions encoded the old chrome

**Interfaces:**
- Consumes: `AppShell { title, version?, user?, homeLabel?, themeLabels?,
  signOutLabel?, sidebar?, children }`, `SidebarGroup { label?, children }`;
  the `app.header.sign_out` key from Task 2.
- Produces: routes render directly in the canvas `main` (each route keeps
  its own `p-8`).

- [ ] **Step 1: Rewrite `Shell.tsx`'s returned JSX** (imports: drop
  `AppHeader`, add `AppShell`; hooks unchanged; keep the comment about
  reusing `/config`'s version):

```tsx
  return (
    <AppShell
      title="chorus"
      user={whoami?.display_name ?? whoami?.username}
      version={config.version ? `v${config.version}` : undefined}
      homeLabel={t('app.header.home')}
      themeLabels={{
        system: t('app.header.theme.system'),
        light: t('app.header.theme.light'),
        dark: t('app.header.theme.dark'),
      }}
      signOutLabel={t('app.header.sign_out')}
      sidebar={<Sidebar />}
    >
      {children}
    </AppShell>
  )
```

- [ ] **Step 2: Rework `Sidebar.tsx`.** Root
  `<aside className="w-64 shrink-0 border-r border-border flex flex-col gap-4 bg-muted p-4">`
  becomes `<div className="flex min-h-0 flex-1 flex-col gap-4">` (AppShell's
  own `aside` provides width/padding/gap/scroll; the sidebar sits
  transparent on the chrome). Each grouped block

```tsx
        <nav key={group.groupKey} className="flex flex-col gap-1">
          <p className="px-3 text-[11px] uppercase tracking-wider text-muted-foreground">
            {t(group.groupKey)}
          </p>
          {group.items.map(...)}
        </nav>
```

becomes the shared primitive:

```tsx
        <SidebarGroup key={group.groupKey} label={t(group.groupKey)}>
          {group.items.map(({ labelKey, to }) => (
            <NavLink key={to} to={to} className={navClass}>
              {t(labelKey)}
            </NavLink>
          ))}
        </SidebarGroup>
```

(import `SidebarGroup` from `@infra/ui`; the top-level pair and the
conditional ingestion `nav` blocks stay hand-rolled `nav`s as today).
Delete the `mt-auto pt-4` `VersionBadge` block, its import, and
`frontend/src/components/VersionBadge.tsx` itself (`git rm`; the header
already shows the version — the duplicate goes away).

- [ ] **Step 3: Add the missing dark accent** — in
  `frontend/src/styles/globals.css` after the `:root` block:

```css
/* Dark palette needs a lighter accent for AA as text on the dark
   background (the base 58% only clears ~3.1:1 there). Mirrors
   theme.css's attribute/media cascade. */
:root[data-theme='dark'] {
  --app-accent: hsl(262 83% 70%);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
    --app-accent: hsl(262 83% 70%);
  }
}
```

- [ ] **Step 4: Update layout tests** — identity-as-text assertions become
  `getByRole('button', { name: ... })`; assertions on the old aside classes
  or the sidebar version badge are removed/re-targeted (version asserted
  once, in the header). Keep all behavioral assertions (nav links, groups,
  conditional ingestion link).

- [ ] **Step 5: Run the gates**

Run: `cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm build`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src frontend/package.json 2>/dev/null; git add -A frontend/src
git commit -m "feat(frontend): adopt AppShell chrome with SidebarGroup nav; dedupe version; dark accent"
```

---

### Task 4: PageHeader rhythm

**Files:**
- Modify: `frontend/src/components/ToolScreen.tsx:92-97`
- Modify: `frontend/src/routes/Landing.tsx:56-59`
- Modify: `frontend/src/routes/Agent.tsx:84-89`
- Modify: any other route file rendering the hand-rolled
  `<div><h1 className="text-2xl font-semibold">…</h1><p className="text-sm text-muted-foreground mt-1">…</p></div>`
  pattern (find them with
  `grep -rln 'text-2xl font-semibold' frontend/src/routes frontend/src/components`)

**Interfaces:**
- Consumes: `PageHeader { title, caption? }` from `@infra/ui`.

- [ ] **Step 1: Swap each title+caption block** for
  `<PageHeader title={…} caption={…} />` with the same i18n expressions
  (caption omitted where none exists today). Specifics:
  - `ToolScreen.tsx`: title `t(spec.titleKey)`, caption
    `spec.captionKey ? t(spec.captionKey) : undefined` — covers all four
    generic tool routes at once.
  - `Landing.tsx`: the literal `"chorus"` h1 (duplicate of the shell title)
    becomes `<PageHeader title={t('nav.dashboard')} caption={t('landing.caption')} />`.
  - `Agent.tsx`: `<PageHeader title={t('agent.title')} caption={t('agent.caption')} />`,
    and the container class `p-8 space-y-6 max-w-3xl` becomes
    `mx-auto w-full max-w-3xl p-8 space-y-6` — the conversation view is
    reading content, so it clamps CENTERED instead of left-hugging.

- [ ] **Step 2: Run the gates**

Run: `cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm build`
Expected: green; update only assertions that encoded the old markup (an
`h1` query still passes — PageHeader renders an `h1`).

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): PageHeader rhythm; centered agent reading measure"
```

---

### Task 5: Release bump + verify

**Files:**
- Modify: `pyproject.toml:3` (`version = "0.4.0"` → `"0.5.0"`)

- [ ] **Step 1: Bump** `[project].version` to `0.5.0`.

- [ ] **Step 2: The full pre-push gate**

```bash
make verify
```

plus `cd frontend && pnpm test` once more. Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: v0.5.0"
```

(include `uv.lock` iff `uv` regenerated it for the version bump)

- [ ] **Step 4: STOP — do not push.** The controller opens the PR.
