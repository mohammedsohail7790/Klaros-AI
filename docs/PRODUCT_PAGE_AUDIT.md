# Klaros page audit: what each page is for, whether it is real, and whether it is automated

Method: read from the code on 2026-10-14. For every one of the 98 pages I extracted the API calls it makes and checked each against the backend routes (420 routes), then checked which backend event handlers produce the data a page shows. Nothing here was inferred from page names.

## Headline findings

1. **There are almost no dummy pages.** 97 of 98 pages call real backend routes (the one exception, `/register`, calls the auth route by another path and works). A few pages call no backend directly because they are static (`/pricing`, `/privacy`, `/terms`) or compose data from a shared hook (`/business/home`, `/business/data`, `/business/analytics`, `/business/integrations`).
2. **"Real" is not the same as "automated".** Most pages are real records the business enters or reviews. Klaros itself acts on its own in a short, specific list (below). Everything else is the owner or staff doing the work, with Klaros keeping the record.
3. **Several things look live but depend on an integration the tenant has not connected**: ad analytics, the AI receptionist, outbound messaging, accounting sync, and clinic outreach. Until connected they show zeros or "not connected". That is honest, but it reads as emptiness.
4. **The real problem is count, not fakery.** 98 pages and about 75 sidebar entries exist because each backend capability got its own page. Several are duplicates of each other.

## What Klaros really does on its own (event-driven, no one clicks)

| Trigger | What happens automatically | Where you see it |
|---|---|---|
| A lead is created | AI qualification and scoring; a notification | Leads, Home |
| A lead is qualified, an appointment or job is created or closed, an invoice is created, a payment arrives | Marketing attribution (which campaign produced which revenue) | Marketing |
| A job is closed | Retention work opens (service reminders, review requests, referral hooks) | Retention |
| A quote is accepted | A contract is created from it | Contracts |
| An invoice is sent or a payment is received | Customer and owner notifications | Notifications |
| A payment, refund or deposit is recorded | Sync to QuickBooks, when connected | Finance |
| A job changes state | Customer communication through the communication layer | Jobs |
| An approval is requested, decided, or an exception is raised | Notifications to the right people | Approvals, Exceptions |
| The morning tick | The Morning Brief is generated (when enabled) | Morning Brief |
| Any event matching a published automation or an agent trigger | That automation or agent runs under the approval policy | Automations, Agents |
| Medical Tourism: any lead | Safety screening and consent evidence; referral to a clinic only after the patient approves that clinic | Patients, Today |

Everything not in this table is manual or human-triggered today. In particular, **finding and contacting clinics, sending invoices, running campaigns and chasing overdue invoices are human-started** (clinic search is automated but never auto-verifies; clinic outreach is a sandbox).

## Pages by area

Verdict key: **Keep** (daily use), **Merge** (fold into a hub as a tab), **Hide** (reachable, but not in the sidebar), **Redirect** (duplicate; send to the canonical page).

### Run the business (daily)
| Pages | Purpose | Real? | Automated? | Verdict |
|---|---|---|---|---|
| `/business/home`, `/dashboard`, `/morning-brief`, `/medical-tourism/overview` | Four different "what needs me today" pages | Yes | Brief is scheduled; actions derive from real state | **Merge into one Home** (the version depends on the tenant's configuration) |
| `/leads`, `/leads/[id]` | Everyone who enquired, with the next step | Yes | Created and qualified automatically | Keep |
| `/customers`, `/customers/[id]` | The people you work with, with a 360 view | Yes | Created from converted leads | Keep |
| `/calendar` | Appointments and open slots | Yes | Google Calendar sync if connected | Keep |
| `/jobs`, `/jobs/[id]`, `/operations/workers`, `/exceptions`, `/operations` | Work orders, field team, anything that looks wrong | Yes | Exceptions raised by detection; notifications automatic | **Merge into "Work"** |

### Money
| Pages | Purpose | Real? | Automated? | Verdict |
|---|---|---|---|---|
| `/quotes`, `/quotes/[id]`, `/quotes/view/[id]` | Quote, send, customer accepts | Yes | Acceptance creates a contract | Keep (the public view is customer-facing) |
| `/contracts`, `/contracts/[id]`, `/contracts/view/[id]` | Agreement and internal signature record | Yes | Created from accepted quotes | Keep |
| `/finance/invoices`, `/finance/invoices/[id]` | Invoices with approval, send, payment | Yes | Notifications and QuickBooks sync | Keep |
| `/finance`, `/finance/ar`, `/finance/cash`, `/finance/profitability`, `/vendors` | Overview, receivables ageing, cash forecast, job profitability, vendor bills | Yes | Overdue detection and collection actions are button-triggered | **Merge into "Money" tabs**; drop the `/finance` index |

### Grow (marketing and retention)
| Pages | Purpose | Real? | Automated? | Verdict |
|---|---|---|---|---|
| `/marketing` | Spend, leads, CAC, ROAS | Yes, but shows zeros until an ad platform is connected | Attribution is automatic | Keep as the Growth overview |
| `/marketing/campaigns`, `/marketing/content`, `/marketing/seo` | Campaigns, drafted content, local SEO pages | Yes | AI drafts, human approves | **Merge**: Campaigns; Content & SEO |
| `/marketing/outbound`, `/marketing/nurture`, `/marketing/reactivation` | Lists, follow-up sequences, win-back | Yes; real delivery needs email/SMS configured and consent | Sequences run on schedule once enabled | **Merge into "Outreach"** |
| `/retention`, `/retention/*` (7 pages) | Reminders, reviews, referrals, warranties, risk, opportunities, campaigns | Yes | Opened automatically when a job closes | **Merge into "Customer care"**; the duplicate "Campaigns" and "Referrals" names collide with Marketing and Medical Tourism |

### AI team
| Pages | Purpose | Real? | Automated? | Verdict |
|---|---|---|---|---|
| `/workforce` | The AI receptionist (Halla) status | Yes, but "Not connected" until Halla is linked | Calls become leads once connected | Keep as the AI team home |
| `/approvals` | Decisions the AI or an automation is waiting on | Yes | Created by policy | Keep (it is the human control point) |
| `/agents`, `/agents/new`, `/agents/[id]`, `/automations`, `/business/workflows`, `/settings/automation` | Build and govern agents and automations | Yes | They are the automation | **Merge into "AI team" tabs**; `/business/workflows` duplicates `/automations` |
| `/ai-activity`, `/events` | Audit of AI calls; the event worker | Yes | n/a (observability) | **Hide** (advanced or admin); `/events` is a developer tool |
| `/settings/knowledge`, `/settings/memory`, `/settings/voice` | What the AI knows, remembers, and how the receptionist sounds | Yes | n/a | **Merge into "AI team → Setup"** |

### Build and define the business
| Pages | Purpose | Real? | Verdict |
|---|---|---|---|
| `/business` (start), `/business/discovery`, `/business/blueprint`, `/business/requirements`, `/business/recommendations`, `/business/map`, `/setup`, `/onboarding` | The guided journey from idea to launch | Yes | **Merge into one "Business" area**: a guided path before launch, a profile afterwards |
| `/business/data`, `/business/analytics` | Counts of records and a view of the same operations data | Yes, but duplicates other pages | **Redirect into Home** |
| `/website`, `/website/preview`, `/builder`, `/builder/[id]`, `/w/[tenantId]` | Generate a site, or import your own, and serve it publicly | Yes | **Merge into "Website"** (two tabs: Generated, Imported) |
| `/business/integrations`, `/settings/integrations` | Two integration pages | Yes | **Redirect** `/business/integrations` to settings. The catalogue also lists planned entries that cannot be connected (Jobber, ServiceTitan, and others). They are labelled "Planned", not faked, but they pad the page. |

### Medical Tourism (when the tenant has this configured)
| Pages | Purpose | Real? | Automated? | Verdict |
|---|---|---|---|---|
| `/medical-tourism/overview`, `/pipeline`, `/patients/[id]`, `/tasks`, `/provider-referrals`, `/consultations` | Today, the patient board, a case, tasks, referrals, consultations | Yes | Intake, safety screening, consent, stages | Keep (the pilot navigation already groups these) |
| `/medical-tourism/providers`, `/procedures` | The clinic directory and offers | Yes | Clinic search automated; verification human by design | Keep |
| `/medical-tourism/referrals`, `/revenue` | Commissions and revenue by confirmed stage | Yes | Computed from confirmed outcomes | Keep (the first is named "Referral Commissions" in one nav and "Commissions" in another) |
| `/medical-tourism/leads` | A second place to create patient leads | Yes | n/a | **Redirect to Patients** |

### Settings and public
`/settings/team`, `/billing`, `/account`, `/compliance`, `/integrations`: **Keep**, as one Settings hub. `/login`, `/register`, `/forgot-password`, `/reset-password`, `/accept-invite`, `/pricing`, `/privacy`, `/terms`, `/contact`, `/` : **Keep** (public and auth).

## Proposed structure

**Nine areas instead of about 75 sidebar entries.** Every current URL keeps working. The pages become tabs inside an area, so nothing is deleted and no bookmark breaks.

| Area | Tabs |
|---|---|
| **Home** | The one daily page (the Morning Brief and Action centre built in) |
| **People** | Leads, Customers, Calendar |
| **Work** | Jobs, Workers, Exceptions |
| **Money** | Quotes, Contracts, Invoices, Receivables, Cash, Profitability, Vendors |
| **Growth** | Overview, Campaigns, Content and SEO, Outreach, Customer care |
| **AI team** | Receptionist, Approvals, Agents, Automations, Setup (Knowledge, Memory, Voice), Activity |
| **Website** | Generated, Imported |
| **Business** | Guided setup, Blueprint, Map |
| **Settings** | Team, Billing, Integrations, Compliance, Account |

For a Medical Tourism tenant the same idea applies, driven by configuration: **Patients, Clinics, Money, Business, Settings.** That navigation already exists; it would simply use the same tab pattern.

The navigation should be built from what the tenant has configured (modules enabled, integrations connected), so a business without field jobs never sees Work, and an unconnected integration shows one "Connect" prompt on Home instead of an empty page.

## What I would remove outright

Only things that duplicate another page or are developer tools: `/dashboard`, `/morning-brief` (folded into Home), `/business/data`, `/business/analytics`, `/business/workflows`, `/business/integrations`, `/finance` (index), `/medical-tourism/leads`, and `/events` (hidden). That is nine pages out of 98. The other 89 are real and stay, regrouped.

## What this audit did not verify

- Whether each page works end to end in production against a live database. The backend and frontend test suites pass, and I have checked many pages on a local database, but the Medical Tourism setup form fails on SQLite, so those screens were verified by code and tests only.
- Which integrations are configured on the live tenant. The Render environment holds the answer, and I have not read secrets there.
