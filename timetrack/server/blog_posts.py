"""Static blog content for the public marketing site."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator


def format_blog_date(iso: str) -> str:
    """YYYY-MM-DD → readable date for templates."""
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %d, %Y")


def category_slug(category: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", category.lower())
    return slug.strip("-") or "general"


def blog_categories(posts: list[BlogPost]) -> list[tuple[str, str, int]]:
    """Category label, slug, count — sorted by count then name."""
    counts = Counter(p.category for p in posts)
    items = [(name, category_slug(name), counts[name]) for name in counts]
    return sorted(items, key=lambda x: (-x[2], x[0]))


@dataclass(frozen=True)
class BlogPost:
    slug: str
    title: str
    excerpt: str
    category: str
    author: str
    published: str  # ISO date YYYY-MM-DD
    read_minutes: int
    body_html: str


def _posts() -> list[BlogPost]:
    return [
        BlogPost(
            slug="roll-out-esstracker-30-days",
            title="How to roll out esstracker in 30 days (without a revolt)",
            excerpt="A week-by-week plan for IT, HR, and managers — policy first, pilot team, then company-wide with My Day and gap approvals baked in.",
            category="Implementation",
            author="Ankit Sharma",
            published="2026-03-15",
            read_minutes=8,
            body_html="""
<p class="blog-lead">Most tracking tools fail in the first month because rollout feels like surveillance dropped from the ceiling. esstracker works better when you treat it like any other internal system: clear owners, a small pilot, and feedback before you flip the whole company.</p>
<h2>Week 1 — Policy and stakeholders</h2>
<p>HR drafts what is collected (apps, URLs, idle, optional screenshots), retention, and who has admin access. IT confirms server URL, backups, and agent install path. Pick one executive sponsor who will repeat the same message in town halls.</p>
<h2>Week 2 — Pilot on one team</h2>
<p>Choose 8–15 people who represent different roles (dev, support, design). Install the desktop agent, enable My Day for everyone in the pilot, and turn on gap approvals. Ask managers to approve requests within 24 hours so the workflow feels real.</p>
<h2>Week 3 — Tune categories and office hours</h2>
<p>Review productivity rules with the pilot. Move misclassified apps before you expand. Set IST office hours to match your attendance policy so lateness reports mean something.</p>
<h2>Week 4 — Company-wide</h2>
<p>Send the final policy, login instructions, and a link to download the agent from the signed-in download page. Keep screenshots off unless compliance requires them — you can enable later.</p>
<h2>After launch</h2>
<ul>
<li>Weekly: managers scan presence and gap queues, not individual app minutes.</li>
<li>Monthly: HR spot-checks that Private Time is understood and used.</li>
<li>Quarterly: revisit app categories with team leads.</li>
</ul>
<p><strong>Tip:</strong> Run parallel with old timesheets for two weeks, compare totals, then retire the spreadsheet.</p>
""",
        ),
        BlogPost(
            slug="weekly-manager-review-checklist",
            title="The 15-minute weekly review managers actually have time for",
            excerpt="Five screens in esstracker that tell you if your team needs help — without opening every person's timeline.",
            category="Management",
            author="Sneha Reddy",
            published="2026-03-12",
            read_minutes=5,
            body_html="""
<p class="blog-lead">You do not need a two-hour deep dive every Friday. A short, repeatable checklist keeps accountability fair and prevents surprise escalations on Monday.</p>
<h2>1. Presence board (2 min)</h2>
<p>Who was late or offline during core hours? Note patterns, not one-offs. If someone is consistently late, schedule a 1:1 — the data is a conversation starter, not a verdict.</p>
<h2>2. Pending gap approvals (3 min)</h2>
<p>Clear the queue. Slow approvals teach people that offline work will not count. Approve with a short comment when the reason is vague but plausible; deny only when policy is clear.</p>
<h2>3. Team productivity trend (3 min)</h2>
<p>Look at the week, not Tuesday afternoon. Compare to the person's own prior weeks before comparing to teammates with different jobs.</p>
<h2>4. Offline / agent stopped alerts (2 min)</h2>
<p>If the agent was stopped or offline for long stretches, check whether it was intentional (Private Time, leave) or a technical issue.</p>
<h2>5. One positive note (5 min)</h2>
<p>Message someone who improved attendance or cleared gaps proactively. Teams that only hear about tracking when something is wrong will resist the tool.</p>
<blockquote class="blog-pullquote">If a metric does not change what you do next week, stop looking at it every day.</blockquote>
""",
        ),
        BlogPost(
            slug="what-the-desktop-agent-tracks",
            title="What the esstracker desktop agent records (and what it never does)",
            excerpt="A plain-language FAQ for employees and IT — active window, idle, optional screenshots, and how Private Time works.",
            category="Product",
            author="ESS Tracker Team",
            published="2026-03-10",
            read_minutes=6,
            body_html="""
<p class="blog-lead">Employees ask fair questions on day one. This is the answer sheet you can paste into your internal wiki.</p>
<h2>What we collect during a normal work session</h2>
<ul>
<li><strong>Active application and window title</strong> — used for productivity categories and My Day.</li>
<li><strong>Keyboard/mouse activity</strong> — to distinguish active desk time from idle; we do not log keystroke content.</li>
<li><strong>Five-minute timeline blocks</strong> — the unit you see on My Day and admin reports.</li>
<li><strong>Optional screenshots</strong> — only if your admin enables them; frequency is set in policy.</li>
</ul>
<h2>What we do not collect</h2>
<ul>
<li>Keystroke logging or clipboard contents.</li>
<li>Email or chat message bodies inside other apps.</li>
<li>Activity while Private Time is active — tracking pauses until you resume.</li>
</ul>
<h2>Where data goes</h2>
<p>The agent sends encrypted events to your company's esstracker server — not a shared multi-tenant cloud. Your admins control access roles and exports.</p>
<h2>Agent stopped or uninstall</h2>
<p>Admins can be notified when tracking stops unexpectedly. That is a reliability feature, not a punishment — fix broken installs quickly so payroll data stays complete.</p>
""",
        ),
        BlogPost(
            slug="automatic-time-tracking-benefits",
            title="7 benefits of automatic time tracking for remote teams",
            excerpt="Manual timesheets break down when people work from home. Here's how automatic desk time fixes payroll, visibility, and trust.",
            category="Remote work",
            author="ESS Tracker Team",
            published="2026-03-08",
            read_minutes=7,
            body_html="""
<p>Remote and hybrid teams still need accurate hours — but asking people to remember what they did at 4 PM on Tuesday does not scale. Automatic time tracking runs in the background from the desktop agent and builds a timeline your managers can trust.</p>
<h2>1. Payroll you can defend</h2>
<p>Desk time is captured in five-minute blocks from real activity, not self-reported guesses. Finance gets numbers that match what actually happened on the machine.</p>
<h2>2. Less micromanagement in chat</h2>
<p>A live presence board shows who is online, idle, or late — without pinging everyone on Slack every hour.</p>
<h2>3. Employees see their own day</h2>
<p>My Day gives each person the same timeline managers see for them. That transparency reduces “am I being watched?” anxiety when you communicate policy clearly.</p>
<h2>4. Gap approvals instead of silent fixes</h2>
<p>Idle gaps can be explained and approved. Offline meetings and client calls become part of the record instead of holes in the report.</p>
<h2>5. Productivity trends, not one-off screenshots</h2>
<p>Productive vs neutral app rules turn raw data into trends you can coach on weekly — not panic over a single bad afternoon.</p>
<h2>6. Self-hosted option</h2>
<p>With esstracker on your own server, workforce data stays under your retention and access policies — important for IT services and regulated industries.</p>
<h2>7. Faster onboarding</h2>
<p>Install the agent, sign in once, and tracking starts. No VPN spreadsheets or shared Excel macros to maintain.</p>
<p><strong>Ready to try it?</strong> Sign in to your company workspace and download the desktop app from your profile menu.</p>
""",
        ),
        BlogPost(
            slug="employee-monitoring-without-losing-trust",
            title="Employee monitoring without losing trust: a practical guide",
            excerpt="Monitoring only works when people understand why it exists. Policies, Private Time, and visible timelines make the difference.",
            category="Culture",
            author="ESS Tracker Team",
            published="2026-03-05",
            read_minutes=6,
            body_html="""
<p>“Employee monitoring” sounds harsh until you frame it as <em>shared visibility</em>: the same data helps employees prove their day and helps managers allocate work fairly.</p>
<h2>Start with written policy</h2>
<p>Document what is collected (apps, sites, idle, optional screenshots), who can see it, and how long you keep it. Share the policy before rollout — not the day after.</p>
<h2>Use Private Time by default in culture</h2>
<p>esstracker includes Private Time so people can pause tracking for personal breaks or sensitive tasks. Normalize using it instead of treating it as suspicious.</p>
<h2>Show employees My Day first</h2>
<p>Let staff explore their own timeline before managers run team reports. Familiarity reduces fear and catches legitimate tracking bugs early.</p>
<h2>Coach with patterns, not single events</h2>
<p>One hour on social media is not a performance case. Weekly productivity %, gap patterns, and lateness trends are fairer conversation starters.</p>
<h2>Self-host for compliance conversations</h2>
<p>When legal or clients ask where data lives, “on our server” is a clear answer. esstracker is built for that deployment model.</p>
""",
        ),
        BlogPost(
            slug="productivity-percentage-explained",
            title="How productivity percentage is calculated (and how to use it fairly)",
            excerpt="Productivity % is powerful when everyone knows the formula. We break down desk time, categories, and office hours.",
            category="Productivity",
            author="ESS Tracker Team",
            published="2026-02-28",
            read_minutes=5,
            body_html="""
<p>Managers love a single number. Employees hate a single number they do not understand. Productivity percentage in esstracker is transparent: productive time divided by desk time for the day, using rules your admin configures.</p>
<h2>Desk time</h2>
<p>Time at the computer with activity or acceptable idle, inside the tracked session. Breaks and long idle may appear as gaps.</p>
<h2>Productive vs neutral vs unproductive</h2>
<p>Apps and domains map to categories in Settings. VS Code might be productive; news sites might be neutral or unproductive depending on role.</p>
<h2>Effectiveness vs productivity</h2>
<p>Effectiveness often compares productive time to an office-hours goal (e.g. eight hours IST). Productivity is relative to time actually at the desk — useful for different conversations.</p>
<h2>Fair use tips</h2>
<ul>
<li>Review categories quarterly with team input.</li>
<li>Do not compare designers to support staff on the same ruleset.</li>
<li>Pair metrics with gap approvals so offline work counts.</li>
</ul>
""",
        ),
        BlogPost(
            slug="self-hosted-time-tracking-why-it-matters",
            title="Why self-hosted time tracking matters for growing companies",
            excerpt="Cloud SaaS is easy to start; self-hosted esstracker puts retention, access, and cost under your control as you scale.",
            category="Security",
            author="ESS Tracker Team",
            published="2026-02-20",
            read_minutes=6,
            body_html="""
<p>When you track apps, titles, and optional screenshots, you are handling sensitive workforce data. Where that database lives — and who can subpoena it — is a business decision.</p>
<h2>Data residency</h2>
<p>Run esstracker on a server in your country or VPC. No third-party multi-tenant pool for employee activity.</p>
<h2>Access control</h2>
<p>Your LDAP, VPN, and backup policies apply. Admins are your employees; roles are enforced inside the app.</p>
<h2>Cost at scale</h2>
<p>Per-seat SaaS fees add up past fifty users. A fixed server plus your existing ops team often wins on TCO for IT services firms.</p>
<h2>Still modern UX</h2>
<p>Self-hosted does not mean ugly: esstracker ships the same admin dashboard, My Day view, and desktop agents as a polished product — you just own the infrastructure.</p>
""",
        ),
        BlogPost(
            slug="gap-approvals-offline-time",
            title="Gap approvals: the right way to handle offline and idle time",
            excerpt="Idle gaps are not failures — they're signals. Let employees explain them and let managers approve in one workflow.",
            category="Workflow",
            author="ESS Tracker Team",
            published="2026-02-12",
            read_minutes=5,
            body_html="""
<p>Every time tracker shows idle. The difference is whether idle becomes <strong>missing data</strong> or <strong>documented offline time</strong>.</p>
<h2>What employees do</h2>
<p>On My Day, click a gap, add a short note (“client call”, “onsite visit”), and submit. The request shows as pending.</p>
<h2>What managers do</h2>
<p>Approvals queue lists team requests with context. Approve or deny — the timeline updates for reporting.</p>
<h2>Why it beats editing spreadsheets</h2>
<p>Audit trail is built in: who asked, who approved, when. No silent edits to a shared file.</p>
<h2>IST office hours</h2>
<p>esstracker aligns timelines to your configured office day so gaps inside core hours stand out for review.</p>
""",
        ),
        BlogPost(
            slug="hybrid-workforce-attendance-ist",
            title="Hybrid workforce attendance with IST office hours",
            excerpt="Late arrivals, presence, and desk time in one view — built for teams that work across office and home.",
            category="HR",
            author="ESS Tracker Team",
            published="2026-02-01",
            read_minutes=4,
            body_html="""
<p>Hybrid policies fail when “present” means different things in HQ vs at home. esstracker uses agent presence plus configured IST office hours so attendance metrics are comparable.</p>
<h2>Arrival and lateness</h2>
<p>First activity of the day vs your start time feeds late lists on the admin dashboard.</p>
<h2>Online vs idle</h2>
<p>Live board shows who is actively working, not just logged in.</p>
<h2>Reports for HR</h2>
<p>Export desk time and attendance patterns for payroll partners without rebuilding data from chat logs.</p>
""",
        ),
        BlogPost(
            slug="screenshots-policy-best-practices",
            title="Screenshot policies that employees accept",
            excerpt="Screenshots are optional in esstracker. If you enable them, here's how to stay transparent and legal.",
            category="Compliance",
            author="ESS Tracker Team",
            published="2026-01-22",
            read_minutes=5,
            body_html="""
<p>Screenshots are the most sensitive feature in any monitoring tool. Use them only when policy requires proof of work — and pair them with clear notice.</p>
<h2>Disclose in writing</h2>
<p>Include frequency, blur rules, and who may view shots in your employee handbook.</p>
<h2>Limit access</h2>
<p>esstracker restricts screenshot review to admin roles — not every teammate.</p>
<h2>Combine with Private Time</h2>
<p>Let people pause tracking for personal tasks so screenshots never capture private moments.</p>
<h2>Review periodically</h2>
<p>If screenshots rarely change a management decision, turn them off. Trust and retention improve.</p>
""",
        ),
        BlogPost(
            slug="compare-manual-timesheets-automatic-tracking",
            title="Manual timesheets vs automatic tracking: a side-by-side comparison",
            excerpt="Still on Excel or end-of-week forms? See what changes when the desktop agent captures time for you.",
            category="Productivity",
            author="ESS Tracker Team",
            published="2026-01-10",
            read_minutes=4,
            body_html="""
<p>Manual timesheets optimize for <em>easy submission</em>, not <em>accurate work</em>. Automatic tracking inverts that: harder to game, easier to audit.</p>
<table>
<thead><tr><th>Manual</th><th>Automatic (esstracker)</th></tr></thead>
<tbody>
<tr><td>Recall at end of week</td><td>Continuous 5-minute blocks</td></tr>
<tr><td>Manager guesswork</td><td>App and site categories</td></tr>
<tr><td>No live view</td><td>Presence board</td></tr>
<tr><td>Offline work lost</td><td>Gap fill + approval</td></tr>
</tbody>
</table>
<p>Migration tip: run both in parallel for two weeks, compare totals, then retire the sheet.</p>
""",
        ),
    ]


_BY_SLUG: dict[str, BlogPost] | None = None


def all_posts() -> list[BlogPost]:
    """Newest first."""
    return sorted(_posts(), key=lambda p: p.published, reverse=True)


def get_post(slug: str) -> BlogPost | None:
    global _BY_SLUG
    if _BY_SLUG is None:
        _BY_SLUG = {p.slug: p for p in _posts()}
    return _BY_SLUG.get(slug)


def iter_posts() -> Iterator[BlogPost]:
    yield from all_posts()
