# Timeforge SaaS platform

Last updated: 2026-08-13

## What you get

| Feature | Status |
|---------|--------|
| Multi-tenant orgs (`Organization` + `organization_id`) | Live |
| Public signup → workspace + first admin | `/signup` |
| Email confirmation + **15-day trial** | `/confirm/<token>` |
| Plans: `trial` / `starter` / `business` | Schema + superadmin |
| Seat limits (`max_seats`) | Enforced on employee create |
| Org access gate (trial/paid/suspended) | Web + agent API |
| Superadmin console | `/superadmin` |
| Per-org tenant folder (`data/tenants/{slug}/`) | Live |
| Row-level isolation on admin lists / approvals / alerts / screenshots | Hardened |

## Flows

1. Visitor opens **Start free trial** → creates org (status `pending_confirm`).
2. Confirmation email → status `trial`, `trial_ends_at` = now + 15 days.
3. Platform superadmin marks paid / extends trial / suspends at `/superadmin`.
4. Expired or suspended workspaces are blocked (web + agent API `402`).

## Superadmin

Promote a user (default username `boss`) once on startup:

```bash
export TIMEFORGE_SUPERADMIN_USER=boss
```

Then sign in and open **Super admin** in the sidebar.

## Notes

- Shared SQLite DB with row-level `organization_id` (not separate DB per tenant yet).
- Usernames are still globally unique.
- Stripe/webhooks are not wired — paid status is set manually in superadmin.
- Existing default org `id=1` stays **active/business** (platform home company).
