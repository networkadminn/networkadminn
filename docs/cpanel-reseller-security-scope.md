# cPanel Reseller Security Controls: What Can Be Applied

This document clarifies which security controls are possible at the reseller
level and which require server-level/root access from the hosting provider.

## 1) HSTS

**Possible with limited scope.**

- HSTS can be implemented per individual cPanel account/site via `.htaccess`
  or account-level Apache configuration.
- A reseller cannot enforce HSTS globally at the WHM/server level without root
  access.
- Each hosted site must be configured individually unless hosting support
  applies server-level changes.

## 2) Security Headers (CSP, X-Frame-Options, X-Content-Type-Options)

**Possible per site via `.htaccess`.**

These headers can be added in each cPanel account:

```apache
Header always set X-Frame-Options "SAMEORIGIN"
Header always set X-Content-Type-Options "nosniff"
Header always set Content-Security-Policy "default-src 'self'"
```

- This is within reseller control on a per-site basis.
- It cannot be enforced globally across all accounts from WHM without
  server-level support.

## 3) Referrer-Policy

**Possible per site via `.htaccess`.**

```apache
Header always set Referrer-Policy "strict-origin-when-cross-origin"
```

- Can be configured per cPanel account/site.
- Not globally enforceable from WHM alone without server-level access.

## 4) SQLi/XSS Protection and WAF

**Partially possible.**

- Parameterized queries/ORM for SQL injection prevention are application-code
  responsibilities.
- Input sanitization/escaping for XSS prevention is also application-code
  responsibility (for example, in WordPress plugins/themes or custom app code).
- WAF is partially manageable:
  - ModSecurity can be enabled/disabled per account in WHM.
  - Server-wide ruleset management and custom global rules typically require
    hosting-provider/root involvement.

## 5) Authentication and Access Control (MFA, IP Restrictions)

**Partially possible.**

- Password policy controls (for example, minimum strength) can be managed via
  WHM password strength configuration.
- cPanel/WHM 2FA can be enabled (and in some setups enforced) at the reseller
  level.
- WHM host access controls can restrict access by IP for admin endpoints.
- Broader network controls (such as provider-level VPN enforcement or
  server-wide IP allowlisting outside reseller scope) require hosting-provider
  coordination.
