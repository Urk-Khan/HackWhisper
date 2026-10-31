# Innoventix Dashboard — Management & Analytics Console

A modern, high-performance operational dashboard for the **Innoventix Hub AI Inbound Voice Agent**. The console provides real-time visibility into incoming telephony calls, booked calendar meetings, warm lead qualification pipelines, and customer support inquiries — powered directly by Supabase.

Built with **Next.js 14 (App Router)**, **TypeScript**, and **Vanilla CSS Modules**.

---

## 🌟 Key Features

### 1. Operations Dashboard (`/`)
- **Executive KPI Cards**: Rolling 24-hour live metrics for **Total Calls**, **Meetings Booked**, **Leads Captured**, and **Customer Support** inquiries.
- **Dual Visual Analytics (50/50 Grid)**:
  - **Call Volume Histogram**: 24-hour visual activity distribution bucketed by hour.
  - **Call Outcomes Pie / Donut Chart**: Real-time proportional breakdown of all call outcomes with semantic colors:
    - 🟢 **Meetings booked** (`#3fb68a` — Teal)
    - 🟡 **Warm leads** (`#e8a33d` — Amber)
    - 🔵 **Customer support** (`#3b82f6` — Blue)
    - 🔷 **Info inquiry** (`#0ea5e9` — Sky Blue)
    - 🟣 **Transferred** (`#8b5cf6` — Purple)
- **Recent Activity Feed**: Instant snapshot of the latest calls and customer interactions with relative timestamps.

### 2. Call Logs & Transcripts (`/calls`)
- **Full Call History**: Detailed audit trail of all inbound calls processed by the voice agent.
- **Search & Filter**: Real-time telephone number search and outcome filtering (`meeting_booked`, `warm_lead`, `info_inquiry`, `transferred`, `dropped_call` / customer support).
- **Expandable Transcripts**: In-depth conversational transcript inspection with formatted timestamps, caller responses, and agent responses.

### 3. Meetings & Bookings (`/bookings`)
- **Automated Bookings**: Synced directly with appointments scheduled through Google Calendar and Cal.com via the voice agent.
- **Optimized Scheduling Format**: Clear time-first ordering (`4:00 PM Monday, Oct 05`) for immediate readability.
- **Direct Meet Access**: One-click Google Meet join links and attendee contact information.

### 4. Leads Management CRM (`/leads`)
- **Warm Lead Pipeline**: Captures callers who showed purchase intent without immediate booking.
- **Status Workflow**: Interactive state management (`warm` ➔ `contacted` ➔ `converted` ➔ `cold`) using Next.js Server Actions.

### 5. Brand Identity & Edge Security (`/login`)
- **Zero-Delay Animated Brand Identity**: Integrated horizontal white Innoventix Hub animated logo, cropped and optimized to display immediately from 0ms without blank delays or layout shifts.
- **Edge Middleware Protection**: Global route gating via Next.js Edge Middleware (`middleware.ts`).
- **Cryptographic Session Signing**: HMAC-SHA256 signed session cookies via the Web Crypto API (`lib/auth.ts`).
- **Zero Client-Side Secret Leakage**: Supabase service keys are strictly isolated to Server Components and Server Actions.

---

## 🏗️ Architecture & Tech Stack

- **Framework**: [Next.js 14](https://nextjs.org/) (App Router, Server Components, Server Actions)
- **Language**: [TypeScript](https://www.typescriptlang.org/) (Strict mode, full type-safety)
- **Styling**: Vanilla CSS Modules (Zero runtime CSS-in-JS overhead, clean custom design tokens)
- **Database**: [Supabase](https://supabase.com/) (PostgreSQL client via direct REST/PostgREST server queries)
- **Runtime**: Node.js & Edge Runtime compatible

---

## 📁 Project Structure

```
Frontend-main_IH/
├── app/
│   ├── api/logout/route.ts            # Secure session termination handler
│   ├── bookings/page.tsx              # Scheduled meetings (4:00 PM Day, Date) & join links
│   ├── calls/page.tsx                 # Call log table with transcript viewer
│   ├── leads/
│   │   ├── actions.ts                 # Server Action for lead status mutation
│   │   └── page.tsx                   # Leads management CRM view
│   ├── login/
│   │   ├── actions.ts                 # Session verification & cookie generation
│   │   ├── login.module.css           # Login page styling
│   │   └── page.tsx                   # Admin authentication screen
│   ├── globals.css                    # Global theme tokens, typography, and resets
│   ├── layout.tsx                     # Master dashboard shell with collapsible sidebar
│   ├── page.module.css                # Dashboard styling with 50/50 dual analytics grid
│   └── page.tsx                       # Dashboard KPI analytics, volume & outcome charts
├── components/
│   ├── CallsTable.tsx                 # Filterable & searchable call records
│   ├── HourlyChart.tsx                # 24-hour visual activity histogram
│   ├── OutcomePieChart.tsx            # Interactive Donut/Pie Chart for call outcomes
│   ├── OutcomePieChart.module.css     # Donut chart SVG & legend styling
│   ├── LeadsTable.tsx                 # Interactive lead status update table
│   ├── Sidebar.tsx                    # Collapsible navigation drawer with animated logo
│   └── StatusBadge.tsx                # Semantic color-coded status badges
├── lib/
│   ├── auth.ts                        # Web Crypto HMAC-SHA256 cookie session engine
│   ├── supabase.ts                    # Authenticated server-side Supabase client
│   └── types.ts                       # TypeScript schema definitions matching Supabase
├── public/
│   ├── innoventix-logo.gif            # Optimized horizontal white animated logo
│   └── logo.gif                       # Fallback brand asset
├── middleware.ts                      # Edge-level authentication route protection
├── next.config.mjs                    # Next.js configuration
├── package.json                       # Application metadata and dependencies
├── package-lock.json                  # Deterministic dependency lockfile
└── tsconfig.json                      # TypeScript compiler configuration
```

---

## 🚀 Getting Started

### 1. Prerequisites
- Node.js 18.17+ or 20+
- npm or yarn

### 2. Installation
Clone the repository and install dependencies:
```bash
npm install
```

### 3. Environment Configuration
Copy `.env.local.example` to `.env.local`:
```bash
cp .env.local.example .env.local
```

Fill in the required configuration variables:

| Variable | Description | Source |
| :--- | :--- | :--- |
| `SUPABASE_URL` | Supabase Project URL (`https://xyz.supabase.co`) | Supabase Dashboard → Settings → API |
| `SUPABASE_SERVICE_KEY` | Service Role secret key (bypasses RLS for admin queries) | Supabase Dashboard → Settings → API |
| `ADMIN_PASSWORD` | Shared administrator password for console access | Internal team secret |
| `SESSION_SECRET` | 32-byte cryptographic secret for HMAC-SHA256 cookie signing | Generate with `openssl rand -hex 32` |

> [!CAUTION]
> Never prefix `SUPABASE_SERVICE_KEY` with `NEXT_PUBLIC_`. This key must remain strictly server-side to protect customer data.

### 4. Running the Development Server
```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser. You will be automatically redirected to `/login` to authenticate.

### 5. Production Build
```bash
npm run build
npm run start
```

---

## 🔒 Security Architecture

1. **Edge-Level Route Gating**: `middleware.ts` intercepts all requests before any page or API route executes, verifying the presence and signature of the session cookie.
2. **Tamper-Proof Sessions**: The session cookie (`innoventix_session`) contains `issuedAt.signature`. The signature is calculated using HMAC-SHA256 over `issuedAt` with `SESSION_SECRET`.
3. **Data Protection**: Sensitive customer information (caller names, phone numbers, transcripts) is strictly rendered through Next.js Server Components without exposing raw database credentials to the client browser.

---

## 🔄 Database Synchronization

The data model defined in `lib/types.ts` directly mirrors the database tables provisioned in the backend's `innoventix_schema.sql`:
- **`calls`**: Audit logs, caller phone numbers, call durations, and conversational outcomes.
- **`meetings`**: Scheduled calendar entries, meeting links, and attendee details.
- **`leads`**: Cold/warm prospective client records captured during voice agent interactions.
- **`customers`**: Existing customer profiles used for voice agent cross-selling logic.

---

## 🌐 Deployment

The application is fully optimized for one-click deployment to **Vercel**, **AWS Amplify**, or any containerized Docker environment:
1. Link your Git repository to your deployment provider.
2. Configure the 4 environment variables (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ADMIN_PASSWORD`, `SESSION_SECRET`).
3. Deploy! Next.js 14 App Router takes care of streaming server-rendered pages and dynamic routes automatically.
